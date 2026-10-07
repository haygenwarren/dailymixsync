"""Command-line interface: python -m daily_mix_sync <command> ..."""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

from . import music_ui
from .apple_music import CatalogError, CatalogSearch, MockCatalog
from .catalog import CatalogLookup, CatalogReport, add_and_confirm, look_up, resolve_missing
from .config import ConfigError, Settings, load_settings
from .database import MappingStore
from .importer import InputError, Playlist, load_playlist
from .matcher import MatchResult, MatchStatus, ScoredCandidate
from .models import SourceTrack
from .music_app import MusicApp, MusicAppError, UnmanagedPlaylistError
from .music_ui import MusicCatalogUI
from .normalize import source_key
from .review import choose_catalog_result, review_results
from .sync import (
    PlaylistWriteError,
    destination_name,
    match_one,
    match_playlist,
    search_term,
    write_playlist,
)

# Mock runs get their own database so made-up track IDs never reach the real cache.
MOCK_DATABASE_PATH = Path("data/mock_mappings.sqlite3")
# The one playlist the music-* commands are allowed to change: "<prefix> TEST".
TEST_PLAYLIST_SUFFIX = "TEST"


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
    return f"{c.title} — {c.artist}{album} {_clock(c.duration_ms)}  (id {c.persistent_id})"


def _print_unresolved(
    heading: str,
    results: list[MatchResult],
    compact: bool = False,
    reasons: dict[SourceTrack, str] | None = None,
) -> None:
    if not results:
        return
    print(f"\n{heading}:")
    for result in results:
        best = result.best
        if compact:
            closest = "" if best is None else (
                f"  (closest: {best.candidate.title}, score {best.score:.1f})"
            )
            print(f"  {result.track.title} — {result.track.artist}{closest}")
        elif best is None:
            print(f"  {_describe_source(result.track)}")
            print("      no search results")
        else:
            print(f"  {_describe_source(result.track)}")
            print(f"      best {best.score:5.1f}  {_describe_candidate(best)}")
            print(f"                  {best.explain()}")
        if reasons and result.track in reasons:
            print(f"      catalog: {reasons[result.track]}")


def _print_report(
    playlist: Playlist,
    results: list[MatchResult],
    details: bool,
    live: bool = False,
    compact: bool = False,
    catalog: CatalogReport | None = None,
) -> None:
    """Print the summary.

    `live` means the Music library was searched, not a fixture. `catalog` is what
    the Apple Music catalog step did, when there was one.
    """
    by_status = {status: [r for r in results if r.status is status] for status in MatchStatus}
    from_catalog = [r for r in results if r.from_catalog]
    would_add = dict(catalog.would_add) if catalog else {}
    not_found = [r for r in by_status[MatchStatus.FAILED] if r.track not in would_add]
    local = lambda status: [r for r in by_status[status] if not r.from_catalog]  # noqa: E731
    counts = [
        ("Tracks found", len(playlist.tracks)),
        ("Cached matches", len(by_status[MatchStatus.CACHED])),
        ("New matches", len(local(MatchStatus.MATCHED))),
    ]
    if local(MatchStatus.MANUAL):
        counts.append(("Manual matches", len(local(MatchStatus.MANUAL))))
    if would_add:
        counts.append(("Would add", len(would_add)))
    elif catalog is not None:
        counts.append(("From catalog", len(from_catalog)))
    counts += [
        ("Needs review", len(by_status[MatchStatus.REVIEW])),
        ("Not in library" if live else "Failed", len(not_found)),
    ]
    if playlist.duplicates:
        counts.append(("Duplicates", playlist.duplicates))
    if playlist.skipped:
        counts.append(("Unusable entries", len(playlist.skipped)))
    stale = sum(result.stale for result in results)
    if stale:
        counts.append(("Stale mappings", stale))
    print(playlist.name)
    print("-" * len(playlist.name))
    for label, count in counts:
        print(f"{label + ':':<18}{count:>4}")
    for reason in playlist.skipped:
        print(f"  unusable: {reason}")
    if stale:
        print(f"  {stale} remembered track(s) had left the library and were matched again")

    if details:
        if any(result.chosen is not None for result in results):
            print("\nMatched:")
        for result in results:
            if result.from_catalog and result.chosen is not None and result.mapping is not None:
                c, m = result.chosen, result.mapping
                album = f" [{c.album}]" if c.album else ""
                print(f"  {_describe_source(result.track)}")
                print(
                    f"      catalog {m.score:5.1f}  {c.title} — {c.artist}{album} "
                    f"{_clock(c.duration_ms)}  (id {c.persistent_id}, {m.method})"
                )
            elif result.status is MatchStatus.MATCHED and result.best is not None:
                print(f"  {_describe_source(result.track)}")
                print(f"      new  {result.best.score:5.1f}  {_describe_candidate(result.best)}")
            elif result.status is MatchStatus.MANUAL and result.mapping is not None:
                picked = next(c for c in result.candidates if c.candidate == result.chosen)
                print(f"  {_describe_source(result.track)}")
                print(f"      manual {picked.score:5.1f}  {_describe_candidate(picked)}")
            elif result.status is MatchStatus.CACHED and result.mapping is not None:
                m = result.mapping
                print(f"  {_describe_source(result.track)}")
                print(
                    f"      cached      id {m.persistent_id}"
                    f"  ({m.method}, score {m.score:.1f}, first matched {m.matched_at[:10]})"
                )
    if catalog and catalog.added:
        print("\nAdded to your library from the Apple Music catalog:")
        for track, found in catalog.added:
            print(f"  {track.title} — {track.artist}  →  {found.title} — {found.artist}")
    if would_add:
        print("\nWould add from the Apple Music catalog:")
        for track, row in would_add.items():
            print(f"  {track.title} — {track.artist}  →  {row.title} — {row.artist}")
    _print_unresolved("Needs review", by_status[MatchStatus.REVIEW], compact)
    if not live:
        _print_unresolved("Failed", not_found, compact)
        return
    reasons = dict(catalog.problems) if catalog else None
    _print_unresolved("Not in the Music library", not_found, compact, reasons)
    if catalog and catalog.stopped:
        print(f"\nThe catalog step ended early: {catalog.stopped}")
    elif not_found and catalog is None:
        print(
            "  The Apple Music catalog was not searched for these. "
            "`sync --catalog` looks for them there."
        )


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


def _interactive() -> bool:
    """Whether there is a person at the keyboard to answer questions."""
    return sys.stdin.isatty()


def _open_catalog(
    args: argparse.Namespace, settings: Settings
) -> tuple[CatalogSearch, Path, str]:
    """What to search, where to remember matches, and a label for the report.

    The Music library unless a mock catalog is named. The two never share a
    database: a made-up mock ID must not be remembered as a real Music track.
    """
    if args.mock_catalog is None:
        music = MusicApp(settings.managed_playlist_prefix)
        return music, args.db or settings.database_path, "Music library"
    database_path = args.db or MOCK_DATABASE_PATH
    if database_path.resolve() == settings.database_path.resolve():
        raise ConfigError(
            f"refusing to store mock matches in the real mapping database {database_path}; "
            "leave --db out or point it somewhere else"
        )
    return MockCatalog.from_file(args.mock_catalog), database_path, f"mock ({args.mock_catalog})"


def _cmd_match(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    playlist = load_playlist(args.playlist)
    catalog, database_path, source = _open_catalog(args, settings)
    with MappingStore(database_path) as store:
        results = match_playlist(playlist, catalog, store, settings)
    _print_report(playlist, results, args.details, live=args.mock_catalog is None)
    print(f"\nCatalog: {source}   Mappings: {database_path}")
    return 0


def _cmd_review(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    playlist = load_playlist(args.playlist)
    catalog, database_path, source = _open_catalog(args, settings)
    with MappingStore(database_path) as store:
        results = match_playlist(playlist, catalog, store, settings)
        pending = sum(result.status is MatchStatus.REVIEW for result in results)
        if pending:
            results = review_results(results, store)
            print()
        else:
            print("Nothing needs review.\n")
    _print_report(playlist, results, args.details, live=args.mock_catalog is None)
    print(f"\nCatalog: {source}   Mappings: {database_path}")
    return 0


def _catalog_ui() -> MusicCatalogUI:
    return MusicCatalogUI()


def _resolve_from_catalog(
    results: list[MatchResult],
    music: MusicApp,
    store: MappingStore,
    settings: Settings,
    args: argparse.Namespace,
) -> tuple[list[MatchResult], CatalogReport]:
    """Look in the Apple Music catalog for the tracks the library does not have."""
    ui = _catalog_ui()
    ui.require_accessibility()
    missing = sum(result.status is MatchStatus.FAILED for result in results)
    print(
        f"Searching the Apple Music catalog for {missing} track(s). Music will come to the "
        "front; please leave the Mac alone until it hands back.",
        flush=True,
    )

    def choose(lookup: CatalogLookup) -> ScoredCandidate | str:
        ui.hand_back()  # the question is asked in the terminal, not in Music
        return choose_catalog_result(lookup)

    ask = not args.dry_run and not args.no_review and _interactive()
    with ui.session():
        return resolve_missing(
            results, music, ui, store, settings,
            dry_run=args.dry_run, choose=choose if ask else None,
        )


def _print_write_failure(destination: str, error: PlaylistWriteError) -> None:
    out = sys.stderr
    print(f"ERROR: updating {destination!r} failed: {error}", file=out)
    count = len(error.previous)
    if error.restored:
        print(f"✓ Its previous contents were restored ({count} track(s)).", file=out)
        return
    print(f"✗ Its previous contents could NOT be restored: {error.restore_problem}", file=out)
    print(
        f"  Manual intervention is required: {destination!r} may now be empty or incomplete.\n"
        f"  It held these {count} track(s):",
        file=out,
    )
    for track in error.previous:
        print(f"    {track.title} — {track.artist}", file=out)


def _cmd_sync(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    playlist = load_playlist(args.playlist)
    music = MusicApp(settings.managed_playlist_prefix)
    destination = args.destination or destination_name(playlist.name, music.managed_prefix)
    if not music.is_managed(destination):
        raise UnmanagedPlaylistError(
            f"refusing to write to playlist {destination!r}: only {music.managed_prefix!r} and "
            f"playlists starting with {music.managed_prefix + ' '!r} may be changed"
        )

    # Everything that can go wrong with matching happens before the playlist is touched.
    database_path = args.db or settings.database_path
    with MappingStore(database_path) as store:
        results = match_playlist(playlist, music, store, settings)
        ask_now = not args.dry_run and not args.no_review and _interactive()
        if ask_now and any(result.status is MatchStatus.REVIEW for result in results):
            results = review_results(results, store)
            print()
        catalog_report = None
        if args.catalog and any(result.status is MatchStatus.FAILED for result in results):
            results, catalog_report = _resolve_from_catalog(results, music, store, settings, args)
    _print_report(
        playlist, results, args.details, live=True, compact=not args.details,
        catalog=catalog_report,
    )
    waiting = sum(result.status is MatchStatus.REVIEW for result in results)
    if waiting:
        print(f"\n{waiting} track(s) need review and are left out. To decide them, run:")
        print(f"  python -m daily_mix_sync review {args.playlist}")

    track_ids = [result.chosen.persistent_id for result in results if result.chosen is not None]
    existing = music.find_playlist(destination)
    state = "will be created" if existing is None else f"{existing.track_count} track(s) now"
    print(f"\nDestination:      {destination}  ({state})")
    print(f"New contents:     {len(track_ids)} of {len(playlist.tracks)} track(s), in playlist order")
    if catalog_report and catalog_report.would_add:
        print(f"                  plus {len(catalog_report.would_add)} that would be added from the catalog")

    if not track_ids:
        print(
            f"error: none of the {len(playlist.tracks)} track(s) could be matched; "
            f"{destination!r} was left alone",
            file=sys.stderr,
        )
        return 1
    if args.dry_run:
        print("\nNo changes made (--dry-run).")
        return 0
    if not args.yes:
        if not _interactive():
            print(
                "error: not confirmed. Replacing a playlist's contents needs a yes at the "
                "prompt, or --yes when there is no one to ask.",
                file=sys.stderr,
            )
            return 1
        if existing is None:
            question = f"Create {destination!r} with these {len(track_ids)} track(s)?"
        else:
            question = f"Replace the contents of {destination!r}?"
        try:
            answer = input(f"\n{question} [y/N] ")
        except EOFError:
            answer = ""
        if answer.strip().lower() not in ("y", "yes"):
            print("Nothing was changed.")
            return 1

    try:
        report = write_playlist(music, destination, track_ids)
    except PlaylistWriteError as error:
        _print_write_failure(destination, error)
        return 1
    print()
    if report.created:
        print(f"Created playlist:   {destination}")
    else:
        print(f"Cleared old tracks: {report.previous_count}")
    print(f"Added new tracks:   {report.written}")
    print(f"Verified:           {report.written} / {report.written}, in order")
    print("\n✓ Playlist updated and verified.")
    return 0


# --- Music app commands: for trying the Music integration on its own ----------


def _music(args: argparse.Namespace) -> tuple[Settings, MusicApp]:
    settings = load_settings(args.config)
    return settings, MusicApp(settings.managed_playlist_prefix)


def _test_playlist(music: MusicApp) -> str:
    return f"{music.managed_prefix} {TEST_PLAYLIST_SUFFIX}"


def _duration_arg(text: str) -> int:
    """'3:52' or a number of seconds -> milliseconds."""
    try:
        minutes, _, seconds = text.rpartition(":")
        total = int(minutes or 0) * 60 + float(seconds)
        if total <= 0:
            raise ValueError
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{text!r} is not a duration; use M:SS or seconds"
        ) from None
    return round(total * 1000)


def _find_in_library(args: argparse.Namespace) -> tuple[Settings, MusicApp, MatchResult]:
    """Search the Music library for the track named on the command line; print the scoring."""
    settings, music = _music(args)
    track = SourceTrack(args.title, args.artist, args.album or "", args.duration)
    result = match_one(track, music, settings)
    print(f"Looking for: {_describe_source(track)}")
    print(f"Library search {search_term(track)!r}: {len(result.candidates)} candidate(s)")
    for scored in result.candidates:
        print(f"  {scored.score:5.1f}  {_describe_candidate(scored)}")
        print(f"         {scored.explain()}")

    accept = settings.matching.auto_accept_threshold
    if result.best is None:
        print("Result: not in the Music library.")
    elif result.status is MatchStatus.MATCHED:
        print(f"Result: match (score {result.best.score:.1f}; accepted from {accept:g}).")
    else:
        print(
            f"Result: no confident match (best score {result.best.score:.1f}; "
            f"accepted from {accept:g})."
        )
    return settings, music, result


def _cmd_music_test(args: argparse.Namespace) -> int:
    _, music = _music(args)
    was_running = music.is_running()
    music.launch()
    started = "" if was_running else "  (was not running; started it)"
    playlists = music.playlists()
    managed = [p.name for p in playlists if p.is_plain and music.is_managed(p.name)]
    print(f"Music:      version {music.version()}{started}")
    print(f"Library:    {music.library_size()} tracks")
    print(f"Playlists:  {len(playlists)}")
    print(f"Managed:    {', '.join(managed) or 'none yet'}  (prefix {music.managed_prefix!r})")
    print("OK: Music answered every request.")
    print(f"Window control: {_window_control_status()}")
    return 0


def _window_control_status() -> str:
    """Accessibility permission, which only songs outside the library will need."""
    try:
        allowed = music_ui.accessibility_allowed()
    except MusicAppError as exc:
        return f"could not be checked ({exc})"
    if allowed:
        return "permitted"
    return (
        "not permitted. Only needed for songs that are not in your library:\n"
        "                System Settings → Privacy & Security → Accessibility"
    )


def _cmd_music_playlists(args: argparse.Namespace) -> int:
    _, music = _music(args)
    print("Tracks  Playlist")
    for p in music.playlists():
        notes = []
        if p.kind != "user playlist":
            notes.append(p.kind.removesuffix(" playlist"))
        if p.smart:
            notes.append("smart")
        if p.special_kind != "none":
            notes.append(f"built-in: {p.special_kind}")
        if p.is_plain and music.is_managed(p.name):
            notes.append("managed")
        print(f"{p.track_count:6d}  {p.name}" + (f"  [{', '.join(notes)}]" if notes else ""))
    return 0


def _cmd_music_find(args: argparse.Namespace) -> int:
    _, _, result = _find_in_library(args)
    return 0 if result.status is MatchStatus.MATCHED else 1


def _cmd_music_add_test(args: argparse.Namespace) -> int:
    _, music, result = _find_in_library(args)
    if result.status is not MatchStatus.MATCHED or result.best is None:
        print("Nothing was added.")
        return 1
    chosen = result.best.candidate
    name = _test_playlist(music)
    existed = music.find_playlist(name) is not None
    music.ensure_playlist(name)
    before = music.playlist_tracks(name)
    if any(t.persistent_id == chosen.persistent_id for t in before):
        print(f"{name!r} already contains it; nothing was added.")
        return 0

    missing = music.add_tracks(name, [chosen.persistent_id])
    after = music.playlist_tracks(name)
    added = any(t.persistent_id == chosen.persistent_id for t in after)
    if missing or not added or len(after) != len(before) + 1:
        raise MusicAppError(
            f"Music took the request but {chosen.title!r} did not show up in {name!r} "
            f"({len(before)} track(s) before, {len(after)} after)"
        )
    kind = "existing" if existed else "newly created"
    print(f"Added to {name!r} ({kind} playlist). Checked: it now holds {len(after)} track(s).")
    return 0


def _cmd_music_clear_test(args: argparse.Namespace) -> int:
    _, music = _music(args)
    name = _test_playlist(music)
    if music.find_playlist(name) is None:
        print(f"There is no playlist named {name!r}; nothing to do.")
    elif args.delete:
        music.delete_playlist(name)
        print(f"Deleted {name!r}.")
    else:
        print(f"Removed {music.clear_playlist(name)} track(s) from {name!r}.")
    return 0


# --- Music window commands: the Apple Music catalog, through the Music window --------


def _print_lookup(lookup: CatalogLookup, settings: Settings) -> None:
    track = lookup.track
    print("Apple Music catalog search\n")
    print(f"Looking for: {_describe_source(track)}")
    print(f"Query:       {search_term(track)}\n")
    if not lookup.match.candidates:
        print("Results: none")
    else:
        print("Results, best match first (the results page shows no album or duration):")
    for scored in lookup.match.candidates:
        row = lookup.row(scored)
        print(f"  {scored.score:5.1f}  {row.title} — {row.artist}   (result {row.ordinal} on the page)")
        print(f"         {scored.explain()}")
    accept = settings.matching.auto_accept_threshold
    best = lookup.match.best
    if best is None:
        print("\nResult: nothing found.")
    elif lookup.match.status is MatchStatus.MATCHED:
        row = lookup.row(best)
        print(f"\nResult: {row.title} — {row.artist} would be chosen (score {best.score:.1f}; accepted from {accept:g}).")
    elif lookup.match.status is MatchStatus.REVIEW:
        print(f"\nResult: needs a choice by hand (best score {best.score:.1f}; accepted from {accept:g}).")
    else:
        print(f"\nResult: no acceptable match (best score {best.score:.1f}; accepted from {accept:g}).")


def _cmd_music_ui_inspect(args: argparse.Namespace) -> int:
    ui = _catalog_ui()
    ui.require_accessibility()
    with ui.session():
        report = ui.inspect()
        dumps = [(part, ui.dump(part)) for part in ("toolbar", "pane")] if args.dump else []
    print("What the catalog automation relies on in the Music window:\n")
    for state, item, detail in report:
        mark = {"ok": "ok     ", "missing": "MISSING", "info": "note   "}.get(state, state)
        print(f"  {mark}  {item}: {detail}")
    for part, text in dumps:
        elements = [e.strip(" ,") for e in text.split(" of window Music of application process Music")]
        print(f"\nEvery element under the {part} ({len([e for e in elements if e])}):")
        for element in elements:
            if element:
                print(f"  {element}")
    missing = [item for state, item, _ in report if state == "missing"]
    if missing:
        print(f"\n{len(missing)} expected part(s) not found. Catalog search will not work until "
              "music_ui.py is brought in line with this layout.")
        return 1
    print("\nEverything the catalog automation needs is in place.")
    return 0


def _cmd_music_catalog_search(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    ui = _catalog_ui()
    ui.require_accessibility()
    track = SourceTrack(args.title, args.artist, args.album or "", args.duration)
    with ui.session():
        lookup = look_up(track, ui, settings)
    _print_lookup(lookup, settings)
    print("Nothing was changed.")
    return 0 if lookup.match.status is MatchStatus.MATCHED else 1


def _cmd_music_catalog_add_test(args: argparse.Namespace) -> int:
    settings, music = _music(args)
    ui = _catalog_ui()
    ui.require_accessibility()
    track = SourceTrack(args.title, args.artist, args.album or "", args.duration)
    with ui.session():
        lookup = look_up(track, ui, settings)
    _print_lookup(lookup, settings)

    by_hand = False
    picked = lookup.match.best
    if lookup.match.status is MatchStatus.REVIEW and _interactive():
        answer = choose_catalog_result(lookup)
        picked, by_hand = (answer, True) if isinstance(answer, ScoredCandidate) else (None, False)
    elif lookup.match.status is not MatchStatus.MATCHED:
        picked = None
    if picked is None:
        print("Nothing was added.")
        return 1
    row = lookup.row(picked)

    if not args.yes:
        if not _interactive():
            print(
                "error: not confirmed. Adding a song to your library needs a yes at the "
                "prompt, or --yes when there is no one to ask.",
                file=sys.stderr,
            )
            return 1
        try:
            answer = input(f"\nAdd {row.title!r} by {row.artist!r} to your Music library? [y/N] ")
        except EOFError:
            answer = ""
        if answer.strip().lower() not in ("y", "yes"):
            print("Nothing was added.")
            return 1

    with ui.session():
        found, added = add_and_confirm(track, lookup, picked, by_hand, music, ui, settings)
    print()
    print("Add to Library was chosen in Music." if added else
          "Music shows this song as already in your library; nothing was added.")
    if found is None:
        print(
            f"error: no matching track showed up in the library within "
            f"{settings.catalog_wait_s} seconds.",
            file=sys.stderr,
        )
        return 1
    album = f" [{found.album}]" if found.album else ""
    print(f"The library now has: {found.title} — {found.artist}{album} {_clock(found.duration_ms)}")
    print(f"Persistent ID:       {found.persistent_id}")
    print("It was not added to any playlist.")
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

    catalog = argparse.ArgumentParser(add_help=False)
    catalog.add_argument("playlist", type=Path, help="playlist export (JSON)")
    catalog.add_argument(
        "--mock-catalog", type=Path, metavar="FILE",
        help="search this JSON fixture instead of the Music library",
    )
    catalog.add_argument(
        "--db", type=Path, metavar="FILE",
        help="mapping database (default: the configured one, or "
        f"{MOCK_DATABASE_PATH} with --mock-catalog)",
    )
    catalog.add_argument(
        "--details", action="store_true", help="also list every matched track and its choice"
    )

    match = commands.add_parser(
        "match", parents=[common, catalog],
        help="match a playlist export against the Music library and remember the matches",
    )
    match.set_defaults(handler=_cmd_match)

    review = commands.add_parser(
        "review", parents=[common, catalog],
        help="match a playlist export, then choose by hand for the tracks that need review",
    )
    review.set_defaults(handler=_cmd_review)

    sync = commands.add_parser(
        "sync", parents=[common],
        help="match a playlist export and write the result to its managed playlist in Music",
    )
    sync.add_argument("playlist", type=Path, help="playlist export (JSON)")
    sync.add_argument(
        "--dry-run", action="store_true",
        help="match and report, but do not create or change any playlist",
    )
    sync.add_argument(
        "--yes", action="store_true", help="replace the playlist's contents without asking"
    )
    sync.add_argument(
        "--no-review", action="store_true",
        help="do not ask about tracks that need review; leave them out",
    )
    sync.add_argument(
        "--into", dest="destination", metavar="NAME",
        help="write to this managed playlist instead of the one named after the export",
    )
    sync.add_argument(
        "--catalog", action=argparse.BooleanOptionalAction, default=False,
        help="look in the Apple Music catalog for tracks the library lacks, and add the "
        "matches to the library (operates the Music window; default: --no-catalog)",
    )
    sync.add_argument("--db", type=Path, metavar="FILE", help="mapping database")
    sync.add_argument(
        "--details", action="store_true", help="also list every matched track and its choice"
    )
    sync.set_defaults(handler=_cmd_sync)

    music_test = commands.add_parser(
        "music-test", parents=[common], help="check that the Music app can be reached"
    )
    music_test.set_defaults(handler=_cmd_music_test)

    music_playlists = commands.add_parser(
        "music-playlists", parents=[common], help="list the playlists in the Music app"
    )
    music_playlists.set_defaults(handler=_cmd_music_playlists)

    track = argparse.ArgumentParser(add_help=False)
    track.add_argument("title", help="track title")
    track.add_argument("artist", help="artist; several can be given comma separated")
    track.add_argument("--album", help="album, to tell releases apart")
    track.add_argument(
        "--duration", type=_duration_arg, metavar="M:SS", help="length, to tell versions apart"
    )

    music_find = commands.add_parser(
        "music-find", parents=[common, track],
        help="look for one track in the Music library and show how candidates score",
    )
    music_find.set_defaults(handler=_cmd_music_find)

    music_add_test = commands.add_parser(
        "music-add-test", parents=[common, track],
        help='find one track and add it to the "<prefix> TEST" playlist',
    )
    music_add_test.set_defaults(handler=_cmd_music_add_test)

    music_clear_test = commands.add_parser(
        "music-clear-test", parents=[common], help='empty the "<prefix> TEST" playlist'
    )
    music_clear_test.add_argument(
        "--delete", action="store_true", help="delete the playlist instead of emptying it"
    )
    music_clear_test.set_defaults(handler=_cmd_music_clear_test)

    ui_inspect = commands.add_parser(
        "music-ui-inspect", parents=[common],
        help="check that the Music window has what catalog search relies on",
    )
    ui_inspect.add_argument(
        "--dump", action="store_true", help="also list every element of the toolbar and main pane"
    )
    ui_inspect.set_defaults(handler=_cmd_music_ui_inspect)

    catalog_search = commands.add_parser(
        "music-catalog-search", parents=[common, track],
        help="search the Apple Music catalog for one song and show the results (changes nothing)",
    )
    catalog_search.set_defaults(handler=_cmd_music_catalog_search)

    catalog_add = commands.add_parser(
        "music-catalog-add-test", parents=[common, track],
        help="find one song in the Apple Music catalog and add it to your library",
    )
    catalog_add.add_argument(
        "--yes", action="store_true", help="add without asking for confirmation"
    )
    catalog_add.set_defaults(handler=_cmd_music_catalog_add_test)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    level = (logging.WARNING, logging.INFO, logging.DEBUG)[min(args.verbose, 2)]
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s", force=True)
    try:
        return args.handler(args)
    except (InputError, ConfigError, CatalogError, MusicAppError) as exc:
        print(f"error: {exc}", file=sys.stderr)
    except sqlite3.Error as exc:
        print(f"error: mapping database problem: {exc}", file=sys.stderr)
    except KeyboardInterrupt:
        # An interrupt while a playlist is being written is handled there, by
        # restoring it. One that arrives here came before or after that.
        print("\ninterrupted", file=sys.stderr)
        return 130
    return 1
