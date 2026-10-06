"""Command-line interface: python -m daily_mix_sync <command> ..."""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

from .apple_music import CatalogError, MockCatalog
from .config import ConfigError, load_settings
from .database import MappingStore
from .importer import InputError, Playlist, load_playlist
from .matcher import MatchResult, MatchStatus, ScoredCandidate
from .models import SourceTrack
from .normalize import source_key
from .sync import match_playlist

# Mock runs get their own database so made-up catalog IDs never reach the real cache.
MOCK_DATABASE_PATH = Path("data/mock_mappings.sqlite3")


def _clock(duration_ms: int | None) -> str:
    if duration_ms is None:
        return "-:--"
    minutes, seconds = divmod(round(duration_ms / 1000), 60)
    return f"{minutes}:{seconds:02d}"


def _describe_source(track: SourceTrack) -> str:
    album = f" [{track.album}]" if track.album else ""
    return f"{track.title} — {track.artist}{album} {_clock(track.duration_ms)}"


def _describe_candidate(scored: ScoredCandidate) -> str:
    c = scored.candidate
    album = f" [{c.album}]" if c.album else ""
    return f"{c.title} — {c.artist}{album} {_clock(c.duration_ms)}  (id {c.catalog_id})"


def _print_unresolved(heading: str, results: list[MatchResult]) -> None:
    if not results:
        return
    print(f"\n{heading}:")
    for result in results:
        print(f"  {_describe_source(result.track)}")
        if result.best is None:
            print("      no search results")
        else:
            print(f"      best {result.best.score:5.1f}  {_describe_candidate(result.best)}")
            print(f"                  {result.best.explain()}")


def _print_report(playlist: Playlist, results: list[MatchResult], details: bool) -> None:
    by_status = {status: [r for r in results if r.status is status] for status in MatchStatus}
    counts = [
        ("Tracks found", len(playlist.tracks)),
        ("Cached matches", len(by_status[MatchStatus.CACHED])),
        ("New matches", len(by_status[MatchStatus.MATCHED])),
        ("Needs review", len(by_status[MatchStatus.REVIEW])),
        ("Failed", len(by_status[MatchStatus.FAILED])),
    ]
    if playlist.duplicates:
        counts.append(("Duplicates", playlist.duplicates))
    if playlist.skipped:
        counts.append(("Unusable entries", len(playlist.skipped)))
    print(playlist.name)
    print("-" * len(playlist.name))
    for label, count in counts:
        print(f"{label + ':':<18}{count:>4}")
    for reason in playlist.skipped:
        print(f"  unusable: {reason}")

    if details:
        resolved = by_status[MatchStatus.CACHED] + by_status[MatchStatus.MATCHED]
        if resolved:
            print("\nMatched:")
        for result in results:
            if result.status is MatchStatus.MATCHED and result.best is not None:
                print(f"  {_describe_source(result.track)}")
                print(f"      new  {result.best.score:5.1f}  {_describe_candidate(result.best)}")
            elif result.status is MatchStatus.CACHED and result.mapping is not None:
                m = result.mapping
                print(f"  {_describe_source(result.track)}")
                print(
                    f"      cached      id {m.apple_catalog_id}"
                    f"  ({m.method}, score {m.score:.1f}, first matched {m.matched_at[:10]})"
                )
    _print_unresolved("Needs review", by_status[MatchStatus.REVIEW])
    _print_unresolved("Failed", by_status[MatchStatus.FAILED])


def _cmd_validate(args: argparse.Namespace) -> int:
    playlist = load_playlist(args.playlist)
    print(f"{playlist.name}: {len(playlist.tracks)} usable track(s)")
    for number, track in enumerate(playlist.tracks, start=1):
        print(f"{number:4d}. {_describe_source(track)}")
        print(f"      key: {source_key(track)}")
    if playlist.duplicates:
        print(f"Duplicates dropped: {playlist.duplicates}")
    for reason in playlist.skipped:
        print(f"Unusable: {reason}")
    return 0 if playlist.tracks else 1


def _cmd_match(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    playlist = load_playlist(args.playlist)
    if args.mock_catalog is None:
        print(
            "error: live Apple Music search is not implemented yet (next milestone).\n"
            "       Run against the offline fixture instead:\n"
            "       --mock-catalog samples/mock_apple_catalog.json",
            file=sys.stderr,
        )
        return 1
    catalog = MockCatalog.from_file(args.mock_catalog)
    database_path = args.db or MOCK_DATABASE_PATH

    with MappingStore(database_path) as store:
        results = match_playlist(playlist, catalog, store, settings)
    _print_report(playlist, results, args.details)
    print(f"\nCatalog: mock ({args.mock_catalog})   Mappings: {database_path}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--config", type=Path, metavar="FILE",
        help="JSON settings file (default: ./config.json when present, else built-in defaults)",
    )
    common.add_argument(
        "-v", "--verbose", action="count", default=0,
        help="-v logs each track's decision, -vv also logs every candidate's score",
    )

    parser = argparse.ArgumentParser(
        prog="daily_mix_sync",
        description="Recreate Spotify Daily Mix playlists in Apple Music from track metadata.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser(
        "validate", parents=[common], help="check a playlist export and list its tracks"
    )
    validate.add_argument("playlist", type=Path, help="playlist export (JSON)")
    validate.set_defaults(handler=_cmd_validate)

    match = commands.add_parser(
        "match", parents=[common],
        help="match a playlist export against the catalog and remember the matches",
    )
    match.add_argument("playlist", type=Path, help="playlist export (JSON)")
    match.add_argument(
        "--mock-catalog", type=Path, metavar="FILE",
        help="search this JSON fixture instead of the real Apple Music catalog",
    )
    match.add_argument(
        "--db", type=Path, metavar="FILE",
        help=f"mapping database (default with --mock-catalog: {MOCK_DATABASE_PATH})",
    )
    match.add_argument(
        "--details", action="store_true", help="also list every matched track and its choice"
    )
    match.set_defaults(handler=_cmd_match)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    level = (logging.WARNING, logging.INFO, logging.DEBUG)[min(args.verbose, 2)]
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s", force=True)
    try:
        return args.handler(args)
    except (InputError, ConfigError, CatalogError) as exc:
        print(f"error: {exc}", file=sys.stderr)
    except sqlite3.Error as exc:
        print(f"error: mapping database problem: {exc}", file=sys.stderr)
    return 1
