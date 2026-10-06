"""Apple Music catalog access.

Only the offline MockCatalog exists so far. The real Apple Music API client will
provide the same search_songs() method, so the rest of the pipeline does not care
which one it is given.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from .models import AppleCandidate
from .normalize import normalize_text


class CatalogError(Exception):
    """The catalog could not be loaded or queried."""


class CatalogSearch(Protocol):
    def search_songs(self, term: str, limit: int) -> list[AppleCandidate]:
        """Return up to `limit` songs for a free-text term, most relevant first."""
        ...


class MockCatalog:
    """Stand-in for Apple Music catalog search, backed by a JSON fixture.

    It is deliberately as undiscerning as a real search box: it returns every song
    that shares at least half of the query's words, in fixture order, including
    live versions, remixes and same-titled songs by other artists. Picking the
    right one is the matcher's job, not the mock's.
    """

    def __init__(self, songs: Sequence[AppleCandidate]) -> None:
        self._songs = list(songs)
        self._words = [
            set(normalize_text(f"{s.title} {s.artist} {s.album}").split()) for s in self._songs
        ]

    @classmethod
    def from_file(cls, path: Path | str) -> MockCatalog:
        path = Path(path)
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))["songs"]
            songs = [
                AppleCandidate(
                    catalog_id=str(e["id"]),
                    title=e["title"],
                    artist=e["artist"],
                    album=e.get("album") or "",
                    duration_ms=e.get("duration_ms"),
                    url=e.get("url"),
                )
                for e in entries
            ]
        except OSError as exc:
            raise CatalogError(f"cannot read mock catalog {path}: {exc}") from exc
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise CatalogError(
                f"mock catalog {path} is malformed ({type(exc).__name__}: {exc}); expected "
                '{"songs": [{"id": ..., "title": ..., "artist": ...}, ...]}'
            ) from exc
        return cls(songs)

    def search_songs(self, term: str, limit: int = 10) -> list[AppleCandidate]:
        query = set(normalize_text(term).split())
        if not query:
            return []
        hits = [
            (len(query & words) / len(query), index)
            for index, words in enumerate(self._words)
            if 2 * len(query & words) >= len(query)
        ]
        hits.sort(key=lambda hit: (-hit[0], hit[1]))
        return [self._songs[index] for _, index in hits[:limit]]
