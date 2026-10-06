"""Load a playlist export: the JSON file the Spotify browser helper will produce.

Expected shape (unknown keys are ignored):

    {
      "playlist_name": "Daily Mix 1",
      "tracks": [
        {"title": "...", "artist": "A, B", "album": "...", "duration_ms": 222000,
         "spotify_track_id": "...", "spotify_url": "https://open.spotify.com/track/..."}
      ]
    }

Only title and artist are required per track.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from .models import SourceTrack
from .normalize import source_key

log = logging.getLogger(__name__)

_SPOTIFY_TRACK_ID = re.compile(
    r"(?:open\.spotify\.com/(?:intl-[a-z-]+/)?track/|spotify:track:)([A-Za-z0-9]+)"
)


class InputError(Exception):
    """The playlist file cannot be used at all."""


@dataclass(frozen=True)
class Playlist:
    name: str
    tracks: tuple[SourceTrack, ...]
    skipped: tuple[str, ...] = ()  # one explanation per entry that had to be left out
    duplicates: int = 0


def _text(entry: dict, key: str) -> str:
    value = entry.get(key)
    return value.strip() if isinstance(value, str) else ""


def _parse_track(entry: object, label: str, playlist_name: str) -> SourceTrack | str:
    """Return the track, or a sentence explaining why the entry is unusable."""
    if not isinstance(entry, dict):
        return f"{label}: expected an object, found {type(entry).__name__}"
    title, artist = _text(entry, "title"), _text(entry, "artist")
    missing = [name for name, value in (("title", title), ("artist", artist)) if not value]
    if missing:
        shown = f" ({title or artist!r})" if title or artist else ""
        return f"{label}{shown}: missing {' and '.join(missing)}"

    duration = entry.get("duration_ms")
    if duration is not None and (
        isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration <= 0
    ):
        log.warning("%s (%r): ignoring invalid duration_ms %r", label, title, duration)
        duration = None

    url = _text(entry, "spotify_url") or None
    track_id = _text(entry, "spotify_track_id") or None
    if track_id is None and url and (match := _SPOTIFY_TRACK_ID.search(url)):
        track_id = match.group(1)

    return SourceTrack(
        title=title,
        artist=artist,
        album=_text(entry, "album"),
        duration_ms=None if duration is None else int(duration),
        spotify_track_id=track_id,
        spotify_url=url,
        playlist_name=playlist_name,
    )


def load_playlist(path: Path | str) -> Playlist:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise InputError(f"cannot read {path}: {exc.strerror or exc}") from exc
    except UnicodeDecodeError as exc:
        raise InputError(f"{path} is not UTF-8 text: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise InputError(f"{path} is not valid JSON: {exc}") from exc

    if not isinstance(data, dict) or not isinstance(data.get("tracks"), list):
        raise InputError(
            f'{path}: expected an object like {{"playlist_name": "...", "tracks": [...]}}'
        )
    if not data["tracks"]:
        raise InputError(f"{path}: the tracks list is empty")

    name = _text(data, "playlist_name")
    if not name:
        name = path.stem
        log.warning("%s has no playlist_name; using the file name %r", path, name)

    tracks: list[SourceTrack] = []
    skipped: list[str] = []
    seen: set[str] = set()
    duplicates = 0
    for number, entry in enumerate(data["tracks"], start=1):
        parsed = _parse_track(entry, f"track #{number}", name)
        if isinstance(parsed, str):
            log.warning("skipping %s", parsed)
            skipped.append(parsed)
            continue
        key = source_key(parsed)
        if key in seen:
            log.info("track #%d (%r) repeats an earlier track; dropped", number, parsed.title)
            duplicates += 1
            continue
        seen.add(key)
        tracks.append(parsed)

    return Playlist(
        name=name, tracks=tuple(tracks), skipped=tuple(skipped), duplicates=duplicates
    )
