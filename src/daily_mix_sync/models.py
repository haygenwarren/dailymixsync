"""Plain data records passed between pipeline stages."""

from __future__ import annotations

from dataclasses import dataclass

AUTO = "auto"
MANUAL = "manual"


@dataclass(frozen=True)
class SourceTrack:
    """One track as extracted from a Spotify playlist page. Metadata only."""

    title: str
    artist: str  # every credited artist, comma separated, as Spotify shows them
    album: str = ""
    duration_ms: int | None = None
    spotify_track_id: str | None = None
    spotify_url: str | None = None
    playlist_name: str = ""


@dataclass(frozen=True)
class AppleCandidate:
    """One track Music can put in a playlist, as returned by a search."""

    persistent_id: str  # Music's persistent ID for the track (made up in the mock)
    title: str
    artist: str
    album: str = ""
    duration_ms: int | None = None
    url: str | None = None


@dataclass(frozen=True)
class Mapping:
    """A remembered source track -> Music track decision."""

    source_key: str
    persistent_id: str
    score: float
    matched_at: str  # ISO-8601 UTC, when this pairing was first stored
    method: str  # AUTO or MANUAL

    @property
    def is_manual(self) -> bool:
        return self.method == MANUAL
