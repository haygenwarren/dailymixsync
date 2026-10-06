"""The matching pipeline: cache lookup -> catalog search -> scoring -> mapping storage."""

from __future__ import annotations

import logging

from .apple_music import CatalogSearch
from .config import Settings
from .database import MappingStore
from .importer import Playlist
from .matcher import MatchResult, MatchStatus, match_track
from .models import AUTO, SourceTrack
from .normalize import normalize_track, source_key

log = logging.getLogger(__name__)


def search_term(track: SourceTrack) -> str:
    """Catalog query for a track: its base title plus the primary artist."""
    n = normalize_track(track.title, track.artist)
    return " ".join([n.title, *n.artists[:1]])


def match_playlist(
    playlist: Playlist, catalog: CatalogSearch, store: MappingStore, settings: Settings
) -> list[MatchResult]:
    """Resolve every track, in playlist order. Only automatic matches are stored."""
    results: list[MatchResult] = []
    for track in playlist.tracks:
        label = f"{track.title!r} by {track.artist!r}"
        mapping = store.get(source_key(track))
        if mapping is not None:
            log.info("cached  %s -> %s (%s)", label, mapping.apple_catalog_id, mapping.method)
            results.append(MatchResult(track, MatchStatus.CACHED, mapping=mapping))
            continue

        term = search_term(track)
        result = match_track(
            track, catalog.search_songs(term, settings.search_limit), settings.matching
        )
        log.debug("search %r returned %d candidate(s)", term, len(result.candidates))
        for scored in result.candidates:
            c = scored.candidate
            log.debug(
                "  %6.2f  %r by %r [%s] id=%s  (%s)",
                scored.score, c.title, c.artist, c.album, c.catalog_id, scored.explain(),
            )

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
                best.candidate.catalog_id, best.score,
            )
        results.append(result)
    return results
