"""The pipeline: cache lookup -> search -> scoring -> mapping storage -> playlist.

Matching (the first half) works with anything that can search for songs. Writing
the playlist (the second half) needs the real Music app.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, replace

from .apple_music import CatalogSearch
from .config import Settings
from .database import MappingStore
from .importer import Playlist
from .matcher import MatchResult, MatchStatus, match_track
from .models import AUTO, AppleCandidate, SourceTrack
from .music_app import MusicApp, MusicAppError
from .normalize import normalize_track, source_key

log = logging.getLogger(__name__)


# --- matching -------------------------------------------------------------------


def search_term(track: SourceTrack) -> str:
    """Catalog query for a track: its base title plus the primary artist."""
    n = normalize_track(track.title, track.artist)
    return " ".join([n.title, *n.artists[:1]])


def match_one(track: SourceTrack, catalog: CatalogSearch, settings: Settings) -> MatchResult:
    """Search for one track and score what comes back. Nothing is stored."""
    term = search_term(track)
    result = match_track(
        track, catalog.search_songs(term, settings.search_limit), settings.matching
    )
    log.debug("search %r returned %d candidate(s)", term, len(result.candidates))
    for scored in result.candidates:
        c = scored.candidate
        log.debug(
            "  %6.2f  %r by %r [%s] id=%s  (%s)",
            scored.score, c.title, c.artist, c.album, c.persistent_id, scored.explain(),
        )
    return result


def match_playlist(
    playlist: Playlist, catalog: CatalogSearch, store: MappingStore, settings: Settings
) -> list[MatchResult]:
    """Resolve every track, in playlist order. Only automatic matches are stored.

    A stored mapping is used only if its track still exists. One that does not is
    deleted and the track is matched from scratch.
    """
    results: list[MatchResult] = []
    for track in playlist.tracks:
        label = f"{track.title!r} by {track.artist!r}"
        key = source_key(track)
        mapping = store.get(key)
        stale = False
        if mapping is not None:
            live = catalog.get_track(mapping.persistent_id)
            if live is not None:
                log.info("cached  %s -> %s (%s)", label, mapping.persistent_id, mapping.method)
                results.append(
                    MatchResult(track, MatchStatus.CACHED, mapping=mapping, chosen=live)
                )
                continue
            log.warning(
                "%s: the remembered %s match (id %s) is no longer in the library; "
                "matching it again",
                label, mapping.method, mapping.persistent_id,
            )
            store.delete(key)
            stale = True

        result = replace(match_one(track, catalog, settings), stale=stale)
        best = result.best
        if result.status is MatchStatus.MATCHED:
            assert best is not None
            store.save(track, best.candidate, best.score, AUTO)
        if best is None:
            log.info("%-7s %s: no search results", result.status.value, label)
        else:
            log.info(
                "%-7s %s -> %r (%s) score %.1f",
                result.status.value, label, best.candidate.title,
                best.candidate.persistent_id, best.score,
            )
        results.append(result)
    return results


# --- writing the playlist ---------------------------------------------------------


def destination_name(source_name: str, prefix: str) -> str:
    """The managed playlist a source playlist is written to.

    The managed prefix goes in front, and whatever the source name shares with the
    end of the prefix is not repeated:

        "Daily Mix 1"          -> "Spotify Daily Mix 1"
        "Spotify Daily Mix 1"  -> "Spotify Daily Mix 1"
        "Discover Weekly"      -> "Spotify Daily Mix Discover Weekly"

    The result always starts with the prefix, so no source name can lead to a
    playlist outside the managed ones.
    """
    words = source_name.split()
    prefix_words = [w.casefold() for w in prefix.split()]
    for size in range(min(len(prefix_words), len(words)), 0, -1):
        if prefix_words[-size:] == [w.casefold() for w in words[:size]]:
            words = words[size:]
            break
    return " ".join([prefix, *words])


@dataclass(frozen=True)
class WriteReport:
    destination: str
    created: bool  # the playlist did not exist before
    previous_count: int
    written: int


class PlaylistWriteError(Exception):
    """Replacing a playlist's contents failed after the playlist had been touched.

    `restored` says whether the previous contents were put back. When they were
    not, `previous` is what the playlist held and `restore_problem` is why.
    """

    def __init__(
        self,
        message: str,
        restored: bool,
        previous: Sequence[AppleCandidate],
        restore_problem: str = "",
    ) -> None:
        super().__init__(message)
        self.restored = restored
        self.previous = list(previous)
        self.restore_problem = restore_problem


class _Mismatch(Exception):
    """Music did what it was asked without error, but the playlist is not as intended."""


def _difference(expected: Sequence[str], actual: Sequence[str]) -> str | None:
    """Describe how a playlist differs from what was intended, or None if it does not."""
    if list(actual) == list(expected):
        return None
    if len(actual) != len(expected):
        return f"Expected {len(expected)} track(s); Music reports {len(actual)}."
    if Counter(actual) == Counter(expected):
        return f"Music reports the right {len(expected)} track(s), but in a different order."
    wrong = sum((Counter(expected) - Counter(actual)).values())
    return f"{wrong} of the {len(expected)} track(s) in the playlist are not the intended ones."


def _contents(music: MusicApp, name: str) -> list[str]:
    return [track.persistent_id for track in music.playlist_tracks(name)]


def _replace_contents(music: MusicApp, name: str, track_ids: Sequence[str]) -> None:
    """Empty the playlist, add the tracks, read it back. Raises unless it is exact."""
    music.clear_playlist(name)
    missing = music.add_tracks(name, track_ids)
    if missing:
        raise _Mismatch(
            f"{len(missing)} track(s) left the Music library before they could be added."
        )
    problem = _difference(track_ids, _contents(music, name))
    if problem:
        raise _Mismatch(problem)


def write_playlist(music: MusicApp, destination: str, track_ids: Sequence[str]) -> WriteReport:
    """Replace the contents of a managed playlist with `track_ids`, in order.

    The playlist is emptied rather than recreated, so it stays the same playlist.
    Its contents are recorded first; if anything goes wrong after that point, or the
    playlist read back afterwards is not exactly what was asked for, the recorded
    contents are put back and PlaylistWriteError is raised. Music offers no
    transactions, so the restore is a best effort and reports its own outcome.
    """
    created = music.find_playlist(destination) is None
    music.ensure_playlist(destination)  # refuses anything that is not managed
    previous = music.playlist_tracks(destination)
    try:
        _replace_contents(music, destination, track_ids)
    except (MusicAppError, _Mismatch, KeyboardInterrupt) as exc:
        reason = "interrupted" if isinstance(exc, KeyboardInterrupt) else str(exc)
        log.info("writing %r failed (%s); restoring its previous contents", destination, reason)
        try:
            _replace_contents(music, destination, [t.persistent_id for t in previous])
        except (MusicAppError, _Mismatch) as restore_exc:
            raise PlaylistWriteError(
                reason, restored=False, previous=previous, restore_problem=str(restore_exc)
            ) from exc
        raise PlaylistWriteError(reason, restored=True, previous=previous) from exc
    return WriteReport(
        destination=destination,
        created=created,
        previous_count=len(previous),
        written=len(track_ids),
    )
