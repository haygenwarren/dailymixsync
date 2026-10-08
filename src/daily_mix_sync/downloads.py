"""Find the playlist exports the browser extension left in the Downloads folder.

This module only reads files. It does not talk to Music, and it never moves, renames
or deletes anything: the exports stay where the browser put them.

    every *.json in the folder
        -> is it one of our exports?          by its contents, not its name
        -> which playlist would it go to?     the usual destination naming
        -> newest export per playlist         exported_at, else the file's own time
        -> recent enough?                     otherwise left out, and said so

Three kinds of file come out of the first step:

- an export: valid JSON that the importer accepts, with Spotify's marks on it;
- unrelated: any other file. Downloads is full of them, and they are passed over;
- a broken export: a file that is recognisably one of ours but cannot be used. These
  are never passed over in silence, because of what would happen next: if the newest
  export of a mix is broken, quietly using the one before it would put an out-of-date
  mix in the playlist. So that mix is left out of the run instead.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .importer import InputError, load_playlist
from .sync import destination_name

log = logging.getLogger(__name__)

# An export of a few thousand tracks is under a megabyte. Anything far beyond that is
# some other JSON file, and is not worth reading to find out.
MAX_EXPORT_BYTES = 20 * 1024 * 1024
# A timestamp this far ahead of the clock is not believed; the file's own time is used.
CLOCK_SLACK = timedelta(minutes=5)

_SPOTIFY_PLAYLIST = re.compile(r"^https://open\.spotify\.com/(?:intl-[a-z-]+/)?playlist/[A-Za-z0-9]+")
# What the start of one of our exports looks like, even when the rest is damaged.
_NAME_IN_TEXT = re.compile(r'"playlist_name"\s*:\s*"((?:[^"\\]|\\.)*)"')
_CHROME_COPY = re.compile(r"^(.*?)(?: \((\d+)\))?$")


class DownloadsError(Exception):
    """The folder itself cannot be read."""


@dataclass(frozen=True)
class FoundExport:
    """A usable export, and where it would be written."""

    path: Path
    playlist_name: str
    destination: str
    when: datetime  # the moment it stands for: exported_at, or the file's own time
    by_file_time: bool  # `when` is the file's modification time
    track_count: int


@dataclass(frozen=True)
class BrokenExport:
    """A file that is recognisably an export but cannot be used."""

    path: Path
    reason: str
    when: datetime  # the file's modification time
    playlist_name: str = ""  # "" when the file does not say
    destination: str = ""  # "" when it cannot be worked out


@dataclass
class Discovery:
    """What was found in one folder, sorted into what to do with it."""

    directory: Path
    max_age_hours: float
    selected: list[FoundExport] = field(default_factory=list)  # newest and recent, one per playlist
    older: list[FoundExport] = field(default_factory=list)  # superseded by a newer export
    stale: list[FoundExport] = field(default_factory=list)  # newest of their playlist, but too old
    # Recent broken exports, each with the good export it stops from being used (if any).
    broken: list[tuple[BrokenExport, FoundExport | None]] = field(default_factory=list)
    older_broken: list[BrokenExport] = field(default_factory=list)  # harmless: something newer is fine
    unrelated: int = 0  # other JSON files, passed over

    @property
    def paths(self) -> list[Path]:
        return [export.path for export in self.selected]


def export_slug(name: str) -> str:
    """The file name the extension gives a playlist, without ".json".

    The same rule as exportFilename() in extension/export_format.js. It is used only
    to tell which playlist a damaged file was meant for, when the file cannot say.
    """
    words = unicodedata.normalize("NFKC", name).casefold()
    words = re.sub(r"['’`]", "", words)
    words = "".join(ch if ch.isalnum() or unicodedata.category(ch).startswith("M") else "_" for ch in words)
    return re.sub(r"_+", "_", words).strip("_")


def _file_slug(path: Path) -> str:
    """A file's name without ".json" and without the " (1)" a browser adds to a copy."""
    return _CHROME_COPY.match(path.stem).group(1).casefold()


def _copy_number(path: Path) -> int:
    """The number a browser put on a repeated download: 2 for "name (2).json", else 0."""
    return int(_CHROME_COPY.match(path.stem).group(2) or 0)


def _exported_at(data: dict, now: datetime) -> datetime | None:
    """The export's own timestamp, if it has a believable one."""
    value = data.get("exported_at")
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if moment.tzinfo is None or moment > now + CLOCK_SLACK:
        return None
    return moment.astimezone(UTC)


def _looks_like_ours(data: object) -> bool:
    """Whether parsed JSON has the outline of an export: a name and a list of tracks."""
    return isinstance(data, dict) and "tracks" in data and "playlist_name" in data


def _has_spotify_marks(data: dict) -> bool:
    source = data.get("source_url")
    if isinstance(source, str) and _SPOTIFY_PLAYLIST.match(source):
        return True
    tracks = data.get("tracks")
    return isinstance(tracks, list) and any(
        isinstance(track, dict) and (track.get("spotify_track_id") or track.get("spotify_url"))
        for track in tracks
    )


def _examine(path: Path, prefix: str, now: datetime) -> FoundExport | BrokenExport | None:
    """Decide what one file is. None means it is not one of ours."""
    try:
        stat = path.stat()
        if not path.is_file() or stat.st_size > MAX_EXPORT_BYTES:
            return None
        raw = path.read_bytes()
    except OSError as exc:
        log.debug("%s: cannot be read (%s); passed over", path.name, exc.strerror or exc)
        return None
    modified = datetime.fromtimestamp(stat.st_mtime, UTC)
    text = raw.decode("utf-8", errors="replace")

    def broken(reason: str, name: str = "") -> BrokenExport:
        destination = destination_name(name, prefix) if name else ""
        return BrokenExport(path, reason, modified, name, destination)

    try:
        data = json.loads(text)
    except ValueError:
        # Not JSON. It is ours only if it starts the way our exports start; the name
        # it carries, if it got that far, says which playlist it was for.
        head = text[:4096]
        if '"playlist_name"' not in head or ('"tracks"' not in text and '"source_url"' not in text):
            return None
        named = _NAME_IN_TEXT.search(head)
        try:
            name = json.loads(f'"{named.group(1)}"').strip() if named else ""
        except ValueError:
            name = ""
        return broken("it is not valid JSON; the file is damaged or incomplete", name)

    if not _looks_like_ours(data) or not _has_spotify_marks(data):
        return None
    name = data["playlist_name"].strip() if isinstance(data["playlist_name"], str) else ""
    if not name:
        # The importer would fall back to the file's name, and a playlist would be
        # made for "daily_mix_1 (1)". An export has to say which playlist it is.
        return broken("it does not say which playlist it is")
    try:
        playlist = load_playlist(path)
    except InputError as exc:
        # The importer's message starts with the path, which is already being shown.
        return broken(str(exc).removeprefix(f"{path}: "), name)
    if not playlist.tracks:
        return broken("none of its entries can be used", name)

    exported = _exported_at(data, now)
    return FoundExport(
        path=path,
        playlist_name=playlist.name,
        destination=destination_name(playlist.name, prefix),
        when=exported or modified,
        by_file_time=exported is None,
        track_count=len(playlist.tracks),
    )


def _natural(text: str) -> list:
    """Sort key that puts "Daily Mix 2" before "Daily Mix 10"."""
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", text)]


def discover(
    directory: Path, prefix: str, max_age_hours: float, now: datetime | None = None
) -> Discovery:
    """Sort the JSON files of `directory` into exports to use and files to leave.

    For each destination playlist the newest export is the candidate: by its
    `exported_at` when it has a believable one, otherwise by the file's modification
    time. It is used if it is no older than `max_age_hours`.
    """
    now = now or datetime.now(UTC)
    directory = directory.expanduser()
    try:
        files = sorted(p for p in directory.iterdir() if p.suffix.casefold() == ".json" and not p.name.startswith("."))
    except OSError as exc:
        raise DownloadsError(f"cannot read {directory}: {exc.strerror or exc}") from exc

    found = Discovery(directory, max_age_hours)
    usable: dict[str, list[FoundExport]] = {}
    damaged: list[BrokenExport] = []
    for path in files:
        result = _examine(path, prefix, now)
        if result is None:
            found.unrelated += 1
        elif isinstance(result, BrokenExport):
            damaged.append(result)
        else:
            usable.setdefault(result.destination.casefold(), []).append(result)

    # Newest first within each playlist. Should two carry the very same time, the
    # browser's own numbering decides: "name (2).json" was downloaded after "name.json".
    newest: dict[str, FoundExport] = {}
    for key, exports in usable.items():
        exports.sort(
            key=lambda e: (e.when, e.path.stat().st_mtime, _copy_number(e.path), e.path.name),
            reverse=True,
        )
        newest[key] = exports[0]
        found.older.extend(exports[1:])

    limit = now - timedelta(hours=max_age_hours)
    blocked: set[str] = set()
    by_slug = {export_slug(export.playlist_name): key for key, export in newest.items()}
    for item in damaged:
        key = item.destination.casefold() if item.destination else by_slug.get(_file_slug(item.path), "")
        good = newest.get(key)
        if good is not None and item.when <= good.when:
            found.older_broken.append(item)  # a newer export of that playlist is fine
        elif item.when < limit:
            found.older_broken.append(item)  # too old to matter either way
        else:
            found.broken.append((item, good))
            if good is not None:
                blocked.add(key)

    for key, export in newest.items():
        if key in blocked:
            continue
        (found.selected if export.when >= limit else found.stale).append(export)
    found.selected.sort(key=lambda e: _natural(e.playlist_name))
    found.stale.sort(key=lambda e: _natural(e.playlist_name))
    found.older.sort(key=lambda e: (_natural(e.playlist_name), e.when))
    return found
