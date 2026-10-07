"""SQLite store for source track -> Music track (persistent ID) mappings."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import AUTO, MANUAL, AppleCandidate, Mapping, SourceTrack
from .normalize import source_key

# The title/artist/album columns are not read back by the program; they are there
# so that `sqlite3 data/mappings.sqlite3 "select * from mappings"` is readable.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS mappings (
    source_key           TEXT PRIMARY KEY,
    music_persistent_id  TEXT NOT NULL,
    score                REAL NOT NULL,
    method               TEXT NOT NULL CHECK (method IN ('auto', 'manual')),
    matched_at           TEXT NOT NULL,
    source_title         TEXT NOT NULL,
    source_artist        TEXT NOT NULL,
    apple_title          TEXT NOT NULL,
    apple_artist         TEXT NOT NULL,
    apple_album          TEXT NOT NULL
)
"""

# Databases written before the switch to Music.app automation called the
# identifier column apple_catalog_id.
_OLD_ID_COLUMN = "apple_catalog_id"

# A manually confirmed mapping is never replaced by an automatic one. matched_at
# keeps its original value for as long as the pairing itself does not change.
_UPSERT = """
INSERT INTO mappings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (source_key) DO UPDATE SET
    music_persistent_id = excluded.music_persistent_id,
    score               = excluded.score,
    method              = excluded.method,
    matched_at          = CASE
        WHEN mappings.music_persistent_id = excluded.music_persistent_id
        THEN mappings.matched_at ELSE excluded.matched_at END,
    source_title        = excluded.source_title,
    source_artist       = excluded.source_artist,
    apple_title         = excluded.apple_title,
    apple_artist        = excluded.apple_artist,
    apple_album         = excluded.apple_album
WHERE NOT (mappings.method = 'manual' AND excluded.method = 'auto')
"""


class MappingStore:
    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path)
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(mappings)")}
        if _OLD_ID_COLUMN in columns:
            self._conn.execute(
                f"ALTER TABLE mappings RENAME COLUMN {_OLD_ID_COLUMN} TO music_persistent_id"
            )
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> MappingStore:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def get(self, key: str) -> Mapping | None:
        row = self._conn.execute(
            "SELECT source_key, music_persistent_id, score, matched_at, method"
            " FROM mappings WHERE source_key = ?",
            (key,),
        ).fetchone()
        return Mapping(*row) if row else None

    def save(
        self, track: SourceTrack, candidate: AppleCandidate, score: float, method: str
    ) -> Mapping:
        """Store a mapping and return what is now on record for the track.

        Committed immediately, so an interrupted run keeps the matches made so far.
        The returned mapping differs from the arguments only when an automatic
        match was refused because a manual one already exists.
        """
        if method not in (AUTO, MANUAL):
            raise ValueError(f"method must be {AUTO!r} or {MANUAL!r}, got {method!r}")
        key = source_key(track)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._conn.execute(
            _UPSERT,
            (
                key, candidate.persistent_id, score, method, now,
                track.title, track.artist, candidate.title, candidate.artist, candidate.album,
            ),
        )
        self._conn.commit()
        mapping = self.get(key)
        assert mapping is not None
        return mapping

    def delete(self, key: str) -> None:
        """Forget a mapping, for instance because its track left the library."""
        self._conn.execute("DELETE FROM mappings WHERE source_key = ?", (key,))
        self._conn.commit()

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM mappings").fetchone()[0]
