"""Resolve tracks through the Apple Music catalog when the library does not have them.

The Music window is used for the two things AppleScript cannot do: search the
catalog and add a song to the library. Everything else stays on the AppleScript
side. Once a song has been added, the library is polled until it shows up there, it
is scored against the source track like any other candidate, and only then is it
remembered, under its real persistent ID.

    source track -> catalog search (UI) -> score results (matcher)
                 -> Add to Library (UI) -> wait for the library (AppleScript)
                 -> score the library track (matcher) -> mapping
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace

from .config import Settings
from .database import MappingStore
from .matcher import MatchResult, MatchStatus, ScoredCandidate, match_track
from .models import AUTO, MANUAL, AppleCandidate, SourceTrack
from .music_app import MusicApp, MusicAppError
from .music_ui import CatalogUIResult, MusicCatalogUI, MusicUILayoutError
from .sync import match_one, search_term

log = logging.getLogger(__name__)

POLL_INTERVAL_S = 1.0

SKIP = "skip"
QUIT = "quit"

# Asked when the catalog has plausible results but none good enough to add unasked.
# Returns the scored result to add, or SKIP, or QUIT to stop using the catalog.
Chooser = Callable[["CatalogLookup"], "ScoredCandidate | str"]


@dataclass(frozen=True)
class CatalogLookup:
    """Catalog results for one track, scored. They carry title and artist only."""

    track: SourceTrack
    match: MatchResult  # status and ranking, as if the results were library candidates
    _rows: dict[int, CatalogUIResult] = field(repr=False, default_factory=dict)

    def row(self, scored: ScoredCandidate) -> CatalogUIResult:
        """The on-screen result a scored candidate stands for."""
        return self._rows[id(scored.candidate)]


@dataclass
class CatalogReport:
    """What the catalog step did, for the summary."""

    added: list[tuple[SourceTrack, AppleCandidate]] = field(default_factory=list)
    would_add: list[tuple[SourceTrack, CatalogUIResult]] = field(default_factory=list)
    problems: list[tuple[SourceTrack, str]] = field(default_factory=list)
    stopped: str = ""  # why the step ended early, if it did


def look_up(track: SourceTrack, ui: MusicCatalogUI, settings: Settings) -> CatalogLookup:
    """Search the catalog for a track and score what comes back. Changes nothing."""
    rows = ui.search_catalog(search_term(track))
    # The results page shows no album and no duration; the matcher leaves those out.
    candidates = [AppleCandidate(persistent_id="", title=r.title, artist=r.artist) for r in rows]
    match = match_track(track, candidates, settings.matching)
    return CatalogLookup(track, match, {id(c): r for c, r in zip(candidates, rows)})


def wait_for_library(
    find: Callable[[], AppleCandidate | None], timeout_s: float
) -> AppleCandidate | None:
    """Call `find` about once a second until it returns a track or time runs out."""
    deadline = time.monotonic() + timeout_s
    while True:
        found = find()
        if found is not None:
            return found
        if time.monotonic() >= deadline:
            return None
        time.sleep(POLL_INTERVAL_S)


def _library_finder(
    track: SourceTrack, music: MusicApp, settings: Settings
) -> Callable[[], AppleCandidate | None]:
    """A check that the library now holds a confident match for `track`."""

    def find() -> AppleCandidate | None:
        result = match_one(track, music, settings)
        return result.chosen if result.status is MatchStatus.MATCHED else None

    return find


def add_and_confirm(
    track: SourceTrack,
    lookup: CatalogLookup,
    picked: ScoredCandidate,
    by_hand: bool,
    music: MusicApp,
    ui: MusicCatalogUI,
    settings: Settings,
) -> tuple[AppleCandidate | None, bool]:
    """Add one catalog result to the library and find it there.

    Returns (library track or None, whether Add to Library was actually chosen).
    The library track is None when the song did not show up in time, or showed up
    but does not match well enough to be used.
    """
    row = lookup.row(picked)
    added = ui.add_to_library(row)
    if by_hand:
        # The person chose this exact result, so that is what to look for; it may
        # deliberately differ from the source (a live version, say).
        wanted = SourceTrack(row.title, row.artist)
    else:
        # Chosen on title and artist alone. Now that the library has the full
        # metadata, it has to stand up as a match for the source track itself.
        wanted = track
    found = wait_for_library(_library_finder(wanted, music, settings), settings.catalog_wait_s)
    return found, added


def _unconfirmed(
    row: CatalogUIResult, added: bool, ui: MusicCatalogUI, settings: Settings
) -> str:
    """Say what is known about a song that was chosen but could not be confirmed."""
    song = f"{row.title} by {row.artist}"
    waited = f"within {settings.catalog_wait_s} seconds"
    if not added:
        return (
            f"Music shows {song} as already in the library, but no track there matches "
            "well enough to use; it was left out"
        )
    try:
        registered = ui.in_library(row)
    except MusicAppError:
        registered = None
    if registered is False:
        return f"Add to Library was chosen for {song}, but Music still offers it; it was left out"
    return (
        f"{song} was added to the library, but no track matching it well enough appeared "
        f"there {waited}; it was left out, and the song stays in your library"
    )


def resolve_missing(
    results: Sequence[MatchResult],
    music: MusicApp,
    ui: MusicCatalogUI,
    store: MappingStore,
    settings: Settings,
    dry_run: bool = False,
    choose: Chooser | None = None,
) -> tuple[list[MatchResult], CatalogReport]:
    """Try the catalog for every track the library could not supply.

    Only tracks whose status is FAILED are touched. A failure on one track is
    recorded and the rest carry on; a sign that the Music window itself is not as
    expected ends the step, since every later track would hit the same wall.
    With `dry_run`, the catalog is searched and nothing is added or remembered.
    """
    resolved = list(results)
    report = CatalogReport()
    for index, result in enumerate(resolved):
        if result.status is not MatchStatus.FAILED:
            continue
        track = result.track
        label = f"{track.title!r} by {track.artist!r}"
        try:
            lookup = look_up(track, ui, settings)
            best = lookup.match.best
            if lookup.match.status is MatchStatus.MATCHED and best is not None:
                picked, by_hand = best, False
            elif lookup.match.status is MatchStatus.REVIEW and choose is not None:
                answer = choose(lookup)
                if answer == QUIT:
                    report.stopped = "stopped at your request"
                    break
                if not isinstance(answer, ScoredCandidate):
                    report.problems.append((track, "catalog results were skipped in review"))
                    continue
                picked, by_hand = answer, True
            elif lookup.match.status is MatchStatus.REVIEW:
                assert best is not None
                report.problems.append(
                    (track, f"the catalog's closest result needs review ({best.candidate.title}, "
                            f"score {best.score:.1f})")
                )
                continue
            else:
                closest = "" if best is None else (
                    f"; closest was {best.candidate.title} by {best.candidate.artist}, "
                    f"score {best.score:.1f}"
                )
                report.problems.append((track, f"no matching song in the catalog{closest}"))
                continue

            row = lookup.row(picked)
            if dry_run:
                report.would_add.append((track, row))
                log.info("catalog %s -> would add %r by %r", label, row.title, row.artist)
                continue

            found, added = add_and_confirm(track, lookup, picked, by_hand, music, ui, settings)
            if found is None:
                report.problems.append((track, _unconfirmed(row, added, ui, settings)))
                continue
            method = MANUAL if by_hand else AUTO
            mapping = store.save(track, found, picked.score, method)
            resolved[index] = replace(
                result,
                status=MatchStatus.MANUAL if by_hand else MatchStatus.MATCHED,
                chosen=found,
                mapping=mapping,
                from_catalog=True,
            )
            report.added.append((track, found))
            log.info("catalog %s -> %r (%s)", label, found.title, found.persistent_id)
        except MusicUILayoutError as exc:
            report.stopped = str(exc)
            break
        except MusicAppError as exc:
            report.problems.append((track, str(exc)))
            log.info("catalog %s: %s", label, exc)
    return resolved, report
