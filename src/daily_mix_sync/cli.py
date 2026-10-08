"""Command-line interface: python -m daily_mix_sync <command> ...

The supported commands (validate, match, review, sync and the music-* helpers) work
on the Music library through AppleScript only. The experimental catalog commands
live in experimental.py and are merely registered here.
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .apple_music import CatalogError, CatalogSearch, MockCatalog
from .config import ConfigError, Settings, load_settings
from .database import MappingStore
from .importer import InputError, Playlist, load_playlist
from .matcher import MatchResult, MatchStatus, ScoredCandidate
from .models import SourceTrack
from .music_app import MusicApp, MusicAppError, MusicPlaylist, UnmanagedPlaylistError
from .normalize import source_key
from .review import REVIEW_CHOICES, review_results, review_until_quit
from .sync import (
    PlaylistWriteError,
    apply_stored,
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


def _how_found(result: MatchResult, scored: ScoredCandidate) -> str:
    """A note for a candidate that only the broader, title-only search turned up."""
    if scored.candidate.persistent_id in result.title_search_ids:
        return "  [found by title search]"
    return ""


def _print_unresolved(heading: str, results: list[MatchResult], compact: bool = False) -> None:
    if not results:
        return
    print(f"\n{heading}:")
    for result in results:
        best = result.best
        if compact:
            closest = "" if best is None else (
                f"  (closest: {best.candidate.title}, score {best.score:.1f})"
            )
            print(f"  - {result.track.title} — {result.track.artist}{closest}")
        elif best is None:
            print(f"  {_describe_source(result.track)}")
            print("      no search results")
        else:
            print(f"  {_describe_source(result.track)}")
            print(f"      best {best.score:5.1f}  {_describe_candidate(best)}{_how_found(result, best)}")
            print(f"                  {best.explain()}")


# Wording for a run against the Music library, and for one against a mock catalog.
_LIBRARY_LABELS = ("Tracks in Spotify export", "Cached library matches", "New library matches", "Not in library")
_MOCK_LABELS = ("Tracks found", "Cached matches", "New matches", "Failed")


def _print_report(
    playlist: Playlist,
    results: list[MatchResult],
    details: bool,
    live: bool = False,
    compact: bool = False,
) -> None:
    """Print the summary. `live` means the Music library was searched, not a fixture.

    Against the library, a track that is not there is not a failure: it is left out
    on purpose, and the wording says so.
    """
    by_status = {status: [r for r in results if r.status is status] for status in MatchStatus}
    tracks, cached, new, absent = _LIBRARY_LABELS if live else _MOCK_LABELS
    counts = [
        (tracks, len(playlist.tracks)),
        (cached, len(by_status[MatchStatus.CACHED])),
        (new, len(by_status[MatchStatus.MATCHED])),
    ]
    if by_status[MatchStatus.MANUAL]:
        counts.append(("Manual matches", len(by_status[MatchStatus.MANUAL])))
    if by_status[MatchStatus.REVIEW] or not live:
        counts.append(("Needs review", len(by_status[MatchStatus.REVIEW])))
    counts.append((absent, len(by_status[MatchStatus.FAILED])))
    if playlist.duplicates:
        counts.append(("Duplicates", playlist.duplicates))
    if playlist.skipped:
        counts.append(("Unusable entries", len(playlist.skipped)))
    stale = sum(result.stale for result in results)
    if stale:
        counts.append(("Stale mappings", stale))
    width = 25 if live else 18
    print(playlist.name)
    print("-" * len(playlist.name))
    for label, count in counts:
        print(f"{label + ':':<{width}}{count:>4}")
    for reason in playlist.skipped:
        print(f"  unusable: {reason}")
    if stale:
        print(f"  {stale} remembered track(s) had left the library and were matched again")

    if details:
        if any(result.chosen is not None for result in results):
            print("\nMatched:")
        for result in results:
            if result.status is MatchStatus.MATCHED and result.best is not None:
                print(f"  {_describe_source(result.track)}")
                print(
                    f"      new  {result.best.score:5.1f}  {_describe_candidate(result.best)}"
                    f"{_how_found(result, result.best)}"
                )
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
    _print_unresolved("Needs review", by_status[MatchStatus.REVIEW], compact)
    if not live:
        _print_unresolved("Failed", by_status[MatchStatus.FAILED], compact)
        return
    _print_unresolved("Not in your Music library, so left out", by_status[MatchStatus.FAILED], compact)
    if by_status[MatchStatus.FAILED]:
        print("  Only songs already in your library are used. Nothing is added to it.")


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
        return music, args.db or settings.database_path, "your Music library"
    database_path = args.db or MOCK_DATABASE_PATH
    if database_path.resolve() == settings.database_path.resolve():
        raise ConfigError(
            f"refusing to store mock matches in the real mapping database {database_path}; "
            "leave --db out or point it somewhere else"
        )
    return (
        MockCatalog.from_file(args.mock_catalog), database_path,
        f"mock catalog ({args.mock_catalog})",
    )


def _cmd_match(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    playlist = load_playlist(args.playlist)
    songs, database_path, source = _open_catalog(args, settings)
    with MappingStore(database_path) as store:
        results = match_playlist(playlist, songs, store, settings)
    _print_report(playlist, results, args.details, live=args.mock_catalog is None)
    print(f"\nSearched: {source}   Mappings: {database_path}")
    return 0


def _cmd_review(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    playlist = load_playlist(args.playlist)
    songs, database_path, source = _open_catalog(args, settings)
    with MappingStore(database_path) as store:
        results = match_playlist(playlist, songs, store, settings)
        pending = sum(result.status is MatchStatus.REVIEW for result in results)
        if pending:
            results = review_results(results, store)
            print()
        else:
            print("Nothing needs review.\n")
    _print_report(playlist, results, args.details, live=args.mock_catalog is None)
    print(f"\nSearched: {source}   Mappings: {database_path}")
    return 0


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


def _managed_destination(music: MusicApp, playlist_name: str, into: str | None) -> str:
    """The playlist an export is written to: `into`, or the one named after the export.

    Refuses anything outside the managed playlists.
    """
    destination = into or destination_name(playlist_name, music.managed_prefix)
    if not music.is_managed(destination):
        raise UnmanagedPlaylistError(
            f"refusing to write to playlist {destination!r}: only {music.managed_prefix!r} and "
            f"playlists starting with {music.managed_prefix + ' '!r} may be changed"
        )
    return destination


def _print_destination(destination: str, existing: MusicPlaylist | None, new_count: int) -> None:
    previous = "(new playlist)" if existing is None else f"{existing.track_count:>4}"
    print(f"\nDestination:\n{destination}\n")
    print(f"{'Previous tracks:':<25}{previous}")
    print(f"{'New tracks:':<25}{new_count:>4}")


NOT_CONFIRMED = (
    "error: not confirmed. Replacing a playlist's contents needs a yes at the "
    "prompt, or --yes when there is no one to ask."
)


def _cmd_sync(args: argparse.Namespace) -> int:
    """Write the songs of each export that are already in the Music library to its playlist.

    Songs the library does not have are left out; nothing is ever added to the
    library, and only AppleScript is used. One export is handled exactly as it
    always was; several are resolved together first and then written one by one.
    """
    if len(args.playlist) == 1:
        return _sync_one(args, args.playlist[0])
    return _sync_several(args, args.playlist)


def _sync_one(args: argparse.Namespace, path: Path) -> int:
    """Sync one export to its playlist: match, report, ask, write."""
    settings = load_settings(args.config)
    playlist = load_playlist(path)
    music = MusicApp(settings.managed_playlist_prefix)
    destination = _managed_destination(music, playlist.name, args.destination)

    # Everything that can go wrong with matching happens before the playlist is touched.
    database_path = args.db or settings.database_path
    with MappingStore(database_path) as store:
        results = match_playlist(playlist, music, store, settings)
        ask_now = not args.dry_run and not args.no_review and _interactive()
        if ask_now and any(result.status is MatchStatus.REVIEW for result in results):
            results = review_results(results, store)
            print()
    _print_report(playlist, results, args.details, live=True, compact=not args.details)
    waiting = sum(result.status is MatchStatus.REVIEW for result in results)
    if waiting:
        print(f"\n{waiting} track(s) need review and are left out. To decide them, run:")
        print(f"  python -m daily_mix_sync review {path}")

    track_ids = [result.chosen.persistent_id for result in results if result.chosen is not None]
    existing = music.find_playlist(destination)
    _print_destination(destination, existing, len(track_ids))

    if not track_ids:
        print(
            f"error: none of the {len(playlist.tracks)} track(s) are in your Music library; "
            f"{destination!r} was left alone",
            file=sys.stderr,
        )
        return 1
    if args.dry_run:
        print("\nNo changes made (--dry-run).")
        return 0
    if not args.yes:
        if not _interactive():
            print(NOT_CONFIRMED, file=sys.stderr)
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
        write_playlist(music, destination, track_ids)
    except PlaylistWriteError as error:
        _print_write_failure(destination, error)
        return 1
    print("\n✓ Playlist updated and verified.")
    return 0


# --- several exports in one run -------------------------------------------------
#
# The order is what keeps this safe: every export is loaded, every destination is
# settled and every track is matched before the first playlist is touched. After
# that each playlist is written on its own, with its own record of what it held, its
# own check, and its own restore. One that fails does not undo or stop the others.


@dataclass
class _Job:
    """One export on its way to one managed playlist, as part of a batch."""

    path: Path
    playlist: Playlist
    destination: str
    existing: MusicPlaylist | None = None  # the destination as it is now, if it exists
    results: list[MatchResult] = field(default_factory=list)
    outcome: str = ""  # what became of it, for the summary
    failed: bool = False  # it was to be written, and was not

    @property
    def track_ids(self) -> list[str]:
        return [r.chosen.persistent_id for r in self.results if r.chosen is not None]

    def count(self, status: MatchStatus) -> int:
        return sum(result.status is status for result in self.results)


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _load_jobs(paths: list[Path], music: MusicApp) -> list[_Job]:
    """Read every export and work out where each goes. Any problem raises."""
    jobs = []
    for path in paths:
        playlist = load_playlist(path)
        if not playlist.tracks:
            raise InputError(f"{path}: none of its entries can be used")
        jobs.append(_Job(path, playlist, _managed_destination(music, playlist.name, None)))
    return jobs


def _clashing_destinations(jobs: list[_Job]) -> list[list[_Job]]:
    """Groups of exports that would be written to the same playlist.

    Names are compared without regard to case: Music does not tell two playlists
    apart by capitals when it looks one up.
    """
    by_destination: dict[str, list[_Job]] = {}
    for job in jobs:
        by_destination.setdefault(job.destination.casefold(), []).append(job)
    return [group for group in by_destination.values() if len(group) > 1]


def _review_batch(jobs: list[_Job], music: MusicApp, store: MappingStore) -> None:
    """Ask about the tracks that need review, one playlist after another.

    A track is asked about once, however many of the playlists it is in. A choice
    is stored at once and given to the other playlists at the end; a track that was
    skipped is not brought up again in this run. Quitting ends the questions for all.
    """
    asked: set[str] = set()
    for job in jobs:
        pending = [
            index
            for index, result in enumerate(job.results)
            if result.status is MatchStatus.REVIEW and source_key(result.track) not in asked
        ]
        if not pending:
            continue
        print(f"\n{job.playlist.name} — needs review")
        reviewed, stopped = review_until_quit([job.results[i] for i in pending], store)
        for index, result in zip(pending, reviewed, strict=True):
            job.results[index] = result
            asked.add(source_key(result.track))
        if stopped:
            break
    for job in jobs:  # a choice made for one playlist settles the same track in the others
        job.results = apply_stored(job.results, music, store)


def _print_plan(jobs: list[_Job], heading: str) -> None:
    width = max(len(job.destination) for job in jobs)
    print(f"\n{heading}\n")
    for job in jobs:
        count = len(job.track_ids)
        if not count:
            continue
        now = "new playlist" if job.existing is None else f"currently {job.existing.track_count}"
        print(f"{job.destination:<{width}}  {count:>4} {'track ' if count == 1 else 'tracks'}  ({now})")
    unchanged = [job for job in jobs if not job.track_ids]
    if unchanged:
        print("\nLeft unchanged:\n")
        for job in unchanged:
            tracks = _plural(len(job.playlist.tracks), "track")
            print(f"{job.destination:<{width}}  none of its {tracks} are in your library")


def _print_summary(jobs: list[_Job], heading: str) -> None:
    width = max(len("Playlist"), *(len(job.playlist.name) for job in jobs))
    header = f"{'Playlist':<{width}}  {'Spotify':>7}  {'In library':>10}   Result"
    print(f"\n{heading}\n")
    print(header)
    print("-" * (len(header) + 14))
    for job in jobs:
        print(
            f"{job.playlist.name:<{width}}  {len(job.playlist.tracks):>7}  "
            f"{len(job.track_ids):>10}   {job.outcome}"
        )
    totals = [
        ("Total Spotify tracks", sum(len(job.playlist.tracks) for job in jobs)),
        ("Already in library", sum(len(job.track_ids) for job in jobs)),
        ("Intentionally left out", sum(job.count(MatchStatus.FAILED) for job in jobs)),
    ]
    waiting = sum(job.count(MatchStatus.REVIEW) for job in jobs)
    if waiting:
        totals.append(("Waiting for review", waiting))
    print()
    for label, count in totals:
        print(f"{label + ':':<25}{count:>4}")


def _write_batch(music: MusicApp, writable: list[_Job]) -> bool:
    """Write each playlist in turn. Returns whether the run was interrupted.

    A failure is reported and the next playlist is tried: each write records what
    the playlist held and puts it back if anything goes wrong, so one failure
    leaves the others as they should be. An interrupt is different. The person has
    asked for the run to stop, so the playlist in hand is restored and the rest are
    not started.
    """
    interrupted = False
    for job in writable:
        if interrupted:
            job.outcome = "– not attempted: the run was interrupted"
            continue
        try:
            report = write_playlist(music, job.destination, job.track_ids)
        except PlaylistWriteError as error:
            _print_write_failure(job.destination, error)
            job.failed = True
            job.outcome = (
                "✗ write failed; previous contents restored"
                if error.restored
                else "✗ write failed; previous contents NOT restored"
            )
            interrupted = isinstance(error.__cause__, KeyboardInterrupt)
        except MusicAppError as error:
            # Raised before the playlist's contents were touched: while finding it,
            # creating it, or reading what it holds.
            print(f"ERROR: {job.destination!r} was not updated: {error}", file=sys.stderr)
            job.failed = True
            job.outcome = "✗ not updated; its contents were not touched"
        except KeyboardInterrupt:
            job.outcome = "– not attempted: the run was interrupted"
            interrupted = True
        else:
            job.outcome = "✓ created" if report.created else "✓ updated"
            print(f"✓ {job.destination}: {_plural(report.written, 'track')} written and verified.")
    return interrupted


def _sync_several(args: argparse.Namespace, paths: list[Path]) -> int:
    """Sync several exports, each to its own playlist, with one question for all."""
    settings = load_settings(args.config)
    if args.destination is not None:
        print(
            f"error: --into names a single playlist, so it cannot be used with {len(paths)} "
            "exports. Each export goes to the playlist named after it; to choose another, "
            "sync that file on its own.",
            file=sys.stderr,
        )
        return 1
    music = MusicApp(settings.managed_playlist_prefix)

    # Whatever is wrong with a file or a destination ends the run here, before Music
    # is asked to change anything.
    jobs = _load_jobs(paths, music)
    clashes = _clashing_destinations(jobs)
    if clashes:
        for group in clashes:
            print(
                f"error: {len(group)} exports would be written to the same playlist, "
                f"{group[0].destination!r}:",
                file=sys.stderr,
            )
            for job in group:
                print(f"  {job.path}  ({job.playlist.name})", file=sys.stderr)
        print(
            "Nothing was changed. Leave one of them out, or sync them one at a time.",
            file=sys.stderr,
        )
        return 1
    for job in jobs:
        job.existing = music.find_playlist(job.destination)

    # One mapping database for all of them: a song in several mixes is matched once.
    database_path = args.db or settings.database_path
    print(f"Matching {len(jobs)} exports against your Music library:", flush=True)
    with MappingStore(database_path) as store:
        for job in jobs:
            job.results = match_playlist(job.playlist, music, store, settings)
            waiting = job.count(MatchStatus.REVIEW)
            review = f", {waiting} for review" if waiting else ""
            print(
                f"  {job.playlist.name}: {len(job.track_ids)} of {len(job.playlist.tracks)} "
                f"in your library{review}",
                flush=True,
            )
        if not args.dry_run and not args.no_review and _interactive():
            _review_batch(jobs, music, store)

    for job in jobs:
        print()
        _print_report(job.playlist, job.results, args.details, live=True, compact=not args.details)
        waiting = job.count(MatchStatus.REVIEW)
        if waiting:
            print(f"\n{waiting} track(s) need review and are left out. To decide them, run:")
            print(f"  python -m daily_mix_sync review {job.path}")
        _print_destination(job.destination, job.existing, len(job.track_ids))
        if not job.track_ids:
            job.outcome = "– left unchanged: nothing in library"
            print(
                f"\nNone of its {_plural(len(job.playlist.tracks), 'track')} are in your Music "
                f"library, so {job.destination!r} is left as it is."
            )

    writable = [job for job in jobs if job.track_ids]
    if not writable:
        _print_summary(jobs, "Batch sync: nothing to write")
        print(
            f"error: none of the tracks in these {len(jobs)} exports are in your Music library; "
            "no playlist was changed",
            file=sys.stderr,
        )
        return 1
    if args.dry_run:
        for job in writable:
            job.outcome = "would be created" if job.existing is None else "would be updated"
        _print_plan(jobs, "Would update:")
        _print_summary(jobs, "Batch dry run")
        print("\nNo changes made (--dry-run).")
        return 0

    _print_plan(jobs, "Ready to update:")
    if not args.yes:
        if not _interactive():
            print(NOT_CONFIRMED, file=sys.stderr)
            return 1
        try:
            answer = input("\nContinue? [y/N] ")
        except EOFError:
            answer = ""
        if answer.strip().lower() not in ("y", "yes"):
            print("Nothing was changed.")
            return 1

    print()
    interrupted = _write_batch(music, writable)
    done = sum(job.outcome.startswith("✓") for job in jobs)
    failed = sum(job.failed for job in jobs)
    unchanged = len(jobs) - len(writable)
    if interrupted:
        heading = "Batch sync interrupted"
    elif failed:
        heading = "Batch sync finished with errors"
    else:
        heading = "Batch sync complete"
    _print_summary(jobs, heading)
    print()
    if failed or interrupted:
        closing = f"{done} of {_plural(len(writable), 'playlist')} updated."
        if failed:
            closing += f" {failed} failed; see the errors above."
        if interrupted:
            closing += " The run was interrupted before the rest were tried."
    else:
        closing = f"{_plural(done, 'playlist')} updated successfully."
    if unchanged:
        whose = "its" if unchanged == 1 else "their"
        closing += f" {unchanged} left unchanged: none of {whose} tracks are in your library."
    print(closing)
    if interrupted:
        return 130
    return 1 if failed else 0


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
    by_title = len(result.title_search_ids)
    print(f"Library search {search_term(track)!r}: {len(result.candidates) - by_title} candidate(s)")
    if result.title_search:
        print(f"Title search {result.title_search!r}: {by_title} more")
    shown = result.candidates[:REVIEW_CHOICES]
    for scored in shown:
        print(f"  {scored.score:5.1f}  {_describe_candidate(scored)}{_how_found(result, scored)}")
        print(f"         {scored.explain()}")
    if len(result.candidates) > len(shown):
        print(f"  ... and {len(result.candidates) - len(shown)} more with lower scores")

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
    return 0


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
        help="write the songs of an export that are already in your Music library to its "
        "managed playlist; songs you do not have are left out",
    )
    sync.add_argument(
        "playlist", type=Path, nargs="+", metavar="EXPORT",
        help="playlist export (JSON). Several can be given: each goes to its own playlist, "
        "and all are matched before any playlist is changed",
    )
    sync.add_argument(
        "--dry-run", action="store_true",
        help="match and report, but do not create or change any playlist",
    )
    sync.add_argument(
        "--yes", action="store_true", help="replace the playlists' contents without asking"
    )
    sync.add_argument(
        "--no-review", action="store_true",
        help="do not ask about tracks that need review; leave them out",
    )
    sync.add_argument(
        "--into", dest="destination", metavar="NAME",
        help="write to this managed playlist instead of the one named after the export "
        "(with one export only)",
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

    # The catalog/window commands. Imported here, last, because nothing above uses them.
    from . import experimental

    experimental.add_commands(commands, common, track)
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
