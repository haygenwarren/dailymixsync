"""Control the macOS Music app through AppleScript.

This is the only module that knows AppleScript or Music's scripting dictionary.
Every command used here was read from the dictionary of the installed app
(Music.app/Contents/Resources/com.apple.Music.sdef) and exercised against
Music 1.6.3 on macOS 26.3.

Safety: every method that changes a playlist refuses unless the playlist is a
managed one. The rule is checked here in Python and again inside the AppleScript
that performs the change.

Text never gets spliced into a script. Scripts are constants and all values travel
as arguments (`on run argv`).
"""

from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from .models import AppleCandidate

DEFAULT_MANAGED_PREFIX = "Spotify Daily Mix"

_US = "\x1f"  # separates fields in a script's reply
_RS = "\x1e"  # separates rows

# Error numbers raised by the scripts below (AppleScript's own are negative).
_ERR_NOT_MANAGED = 9001
_ERR_PLAYLIST_GONE = 9002
_ERR_CHANGED_WHILE_READING = 9003

AUTOMATION_HELP = (
    "macOS has not allowed this program to control Music. Open System Settings → "
    "Privacy & Security → Automation, find the app this was run from (Terminal, "
    "iTerm, Visual Studio Code, ...) and switch on Music."
)
ACCESSIBILITY_HELP = (
    "macOS has not allowed this program to operate other apps' windows. Open System "
    "Settings → Privacy & Security → Accessibility, add the app this was run from "
    "(Terminal, iTerm, Visual Studio Code, ...) and switch it on."
)


class MusicAppError(Exception):
    """Music could not be reached, or refused or failed a request."""


class MusicPermissionError(MusicAppError):
    """macOS blocked the Apple Event: Automation permission is missing."""


class AccessibilityPermissionError(MusicAppError):
    """macOS blocked operating another app's window: Accessibility permission is missing."""


class UnmanagedPlaylistError(MusicAppError):
    """Refused: the playlist is not one this tool is allowed to change."""


# (script, arguments) -> the script's reply as text
ScriptRunner = Callable[[str, Sequence[str]], str]

_OSA_ERROR = re.compile(r"execution error: (?P<message>.*?)\s*\((?P<code>-?\d+)\)\s*$", re.DOTALL)


def error_from_osascript(stderr: str) -> MusicAppError:
    """Turn osascript's stderr into the most specific exception available."""
    stderr = stderr.strip()
    match = _OSA_ERROR.search(stderr)
    if match is None:
        return MusicAppError(f"osascript failed: {stderr or 'no error text'}")
    message, code = match["message"], int(match["code"])
    if code == -1743:
        return MusicPermissionError(AUTOMATION_HELP)
    # System Events reports this under more than one number (-25211, -1719).
    if "not allowed assistive access" in message:
        return AccessibilityPermissionError(ACCESSIBILITY_HELP)
    if code == -1712:
        return MusicAppError(
            "Music did not answer in time. If macOS is showing a permission prompt, "
            "answer it and try again."
        )
    if code in (-600, -609, -10810):
        return MusicAppError(f"Music is not running or could not be started ({message})")
    if code == _ERR_NOT_MANAGED:
        return UnmanagedPlaylistError(f"refused by the safety check in Music: {message}")
    return MusicAppError(f"{message} (AppleScript error {code})")


def run_osascript(script: str, args: Sequence[str] = (), timeout: float = 120.0) -> str:
    # "--" stops osascript from reading an argument such as "-1" as an option.
    command = ["osascript", "-e", script, "--", *args]
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise MusicAppError("osascript was not found; this needs macOS") from exc
    except subprocess.TimeoutExpired as exc:
        raise MusicAppError(
            f"Music did not answer within {timeout:.0f}s. If macOS is showing a "
            "permission prompt, answer it and try again."
        ) from exc
    if proc.returncode != 0:
        raise error_from_osascript(proc.stderr)
    return proc.stdout.removesuffix("\n")


@dataclass(frozen=True)
class MusicPlaylist:
    persistent_id: str
    name: str
    kind: str  # "user playlist" or "folder playlist"
    special_kind: str  # "none", "Music", "Genius", ...
    smart: bool
    track_count: int

    @property
    def is_plain(self) -> bool:
        """An ordinary playlist: not a folder, not smart, not one of Music's own."""
        return self.kind == "user playlist" and self.special_kind == "none" and not self.smart


# --- AppleScript ---------------------------------------------------------------

_HELPERS = """
property US : character id 31
property RS : character id 30

on joined(rows)
	set AppleScript's text item delimiters to RS
	set out to rows as text
	set AppleScript's text item delimiters to ""
	return out
end joined

on milliseconds(d)
	if d is missing value then return ""
	return (round (d * 1000)) as text
end milliseconds

-- Variable names such as "names", "artists" and "albums" must be avoided: inside a
-- tell block they are Music's own search-area constants.
on trackRows(trackIDs, titles, artistNames, albumNames, lengths)
	set rows to {}
	repeat with i from 1 to count of trackIDs
		set end of rows to (item i of trackIDs) & US & (item i of titles) & US & (item i of artistNames) & US & (item i of albumNames) & US & my milliseconds(item i of lengths)
	end repeat
	return my joined(rows)
end trackRows

on playlistWithID(pid)
	tell application "Music"
		set found to (every user playlist whose persistent ID is pid)
		if (count of found) is not 1 then error "the playlist no longer exists" number 9002
		return item 1 of found
	end tell
end playlistWithID

-- The last line of defence before any change: the playlist must still carry the
-- expected name, that name must be a managed one, and it must be an ordinary
-- playlist. Comparisons are case-sensitive.
on managedPlaylist(pid, expectedName, prefix)
	set p to my playlistWithID(pid)
	tell application "Music"
		set actualName to name of p
		considering case
			if actualName is not expectedName then error "expected " & expectedName & " but found " & actualName number 9001
			if not (actualName is prefix or actualName starts with (prefix & " ")) then error actualName & " is not a managed playlist" number 9001
		end considering
		if (class of p is not user playlist) or (smart of p) or (special kind of p is not none) then error actualName & " is not an ordinary playlist" number 9001
	end tell
	return p
end managedPlaylist
"""

_IS_RUNNING = 'return application "Music" is running'
_LAUNCH = 'tell application "Music" to launch'
_VERSION = 'tell application "Music" to return version'
_LIBRARY_SIZE = 'tell application "Music" to return count of tracks of library playlist 1'

# argv: [name] or nothing. A name is matched without regard to case by Music; the
# caller compares exactly.
_PLAYLISTS = _HELPERS + """
on run argv
	set rows to {}
	tell application "Music"
		if (count of argv) is 0 then
			set found to every user playlist
		else
			set found to (every user playlist whose name is (item 1 of argv))
		end if
		repeat with p in found
			set end of rows to (persistent ID of p) & US & (name of p) & US & (class of p as text) & US & (special kind of p as text) & US & (smart of p as text) & US & (count of tracks of p)
		end repeat
	end tell
	return my joined(rows)
end run
"""

# argv: playlist persistent ID
_PLAYLIST_TRACKS = _HELPERS + """
on run argv
	set p to my playlistWithID(item 1 of argv)
	tell application "Music"
		-- Asking an empty playlist for a property of every track is an error, not {}.
		if (count of tracks of p) is 0 then return ""
		-- With fixed indexing off, tracks come back in the order the playlist is
		-- displayed in, which changes when it is sorted by a column. Turn it on for
		-- the read so the order is the playlist's own, and put it back afterwards.
		set wasFixed to fixed indexing
		set fixed indexing to true
		try
			set trackIDs to persistent ID of every track of p
			set titles to name of every track of p
			set artistNames to artist of every track of p
			set albumNames to album of every track of p
			set lengths to duration of every track of p
		on error errorText number errorNumber
			set fixed indexing to wasFixed
			error errorText number errorNumber
		end try
		set fixed indexing to wasFixed
	end tell
	set n to count of trackIDs
	if (count of titles) is not n or (count of artistNames) is not n or (count of albumNames) is not n or (count of lengths) is not n then error "the playlist changed while it was being read" number 9003
	return my trackRows(trackIDs, titles, artistNames, albumNames, lengths)
end run
"""

# argv: search text, maximum rows. Songs only: the library also holds music videos.
_SEARCH = _HELPERS + """
on run argv
	set maxRows to (item 2 of argv) as integer
	set trackIDs to {}
	set titles to {}
	set artistNames to {}
	set albumNames to {}
	set lengths to {}
	tell application "Music"
		repeat with t in (search library playlist 1 for (item 1 of argv))
			if (count of trackIDs) >= maxRows then exit repeat
			if media kind of t is song then
				set end of trackIDs to persistent ID of t
				set end of titles to name of t
				set end of artistNames to artist of t
				set end of albumNames to album of t
				set end of lengths to duration of t
			end if
		end repeat
	end tell
	return my trackRows(trackIDs, titles, artistNames, albumNames, lengths)
end run
"""

# argv: track persistent ID
_HAS_TRACK = """
on run argv
	tell application "Music" to return exists (some track of library playlist 1 whose persistent ID is (item 1 of argv))
end run
"""

# argv: track persistent ID. Replies with one track row, or nothing if it is gone.
_GET_TRACK = _HELPERS + """
on run argv
	tell application "Music"
		set found to (every track of library playlist 1 whose persistent ID is (item 1 of argv))
		if (count of found) is 0 then return ""
		set t to item 1 of found
		return (persistent ID of t) & US & (name of t) & US & (artist of t) & US & (album of t) & US & my milliseconds(duration of t)
	end tell
end run
"""

# argv: name
_CREATE_PLAYLIST = """
on run argv
	tell application "Music" to return persistent ID of (make new user playlist with properties {name:(item 1 of argv)})
end run
"""

# argv: playlist persistent ID, expected name, managed prefix, then track persistent IDs
# Replies with the IDs that are not in the library.
_ADD_TRACKS = _HELPERS + """
on run argv
	set p to my managedPlaylist(item 1 of argv, item 2 of argv, item 3 of argv)
	set notFound to {}
	tell application "Music"
		repeat with i from 4 to count of argv
			set wanted to (every track of library playlist 1 whose persistent ID is (item i of argv))
			if (count of wanted) is 0 then
				set end of notFound to item i of argv
			else
				duplicate (item 1 of wanted) to p
			end if
		end repeat
	end tell
	return my joined(notFound)
end run
"""

# argv: playlist persistent ID, expected name, managed prefix, track persistent ID
# The track is addressed through the playlist, so only the playlist entry goes.
_REMOVE_TRACK = _HELPERS + """
on run argv
	set p to my managedPlaylist(item 1 of argv, item 2 of argv, item 3 of argv)
	tell application "Music"
		set n to count of (every track of p whose persistent ID is (item 4 of argv))
		if n > 0 then delete (every track of p whose persistent ID is (item 4 of argv))
	end tell
	return n
end run
"""

# argv: playlist persistent ID, expected name, managed prefix
_CLEAR_PLAYLIST = _HELPERS + """
on run argv
	set p to my managedPlaylist(item 1 of argv, item 2 of argv, item 3 of argv)
	tell application "Music"
		set n to count of tracks of p
		if n > 0 then delete every track of p
	end tell
	return n
end run
"""

# argv: playlist persistent ID, expected name, managed prefix
_DELETE_PLAYLIST = _HELPERS + """
on run argv
	set p to my managedPlaylist(item 1 of argv, item 2 of argv, item 3 of argv)
	tell application "Music" to delete p
end run
"""


# --- Python side ---------------------------------------------------------------


def _rows(reply: str, width: int) -> list[list[str]]:
    if not reply:
        return []
    rows = [row.split(_US) for row in reply.split(_RS)]
    for row in rows:
        if len(row) != width:
            raise MusicAppError(f"unexpected reply from Music: {row!r}")
    return rows


def _tracks(reply: str) -> list[AppleCandidate]:
    return [
        AppleCandidate(
            persistent_id=persistent_id,
            title=title,
            artist=artist,
            album=album,
            duration_ms=int(float(ms)) if ms else None,
        )
        for persistent_id, title, artist, album, ms in _rows(reply, 5)
    ]


class MusicApp:
    """The Music app on this Mac.

    Reading works on anything. Changing works only on managed playlists: those
    named exactly `managed_prefix`, or `managed_prefix` followed by a space and
    more ("Spotify Daily Mix 1"). "Spotify Daily Mixtape" is not managed.
    """

    def __init__(
        self, managed_prefix: str = DEFAULT_MANAGED_PREFIX, run: ScriptRunner = run_osascript
    ) -> None:
        if not managed_prefix or managed_prefix != managed_prefix.strip():
            raise ValueError("managed playlist prefix must be non-empty, without outer spaces")
        self.managed_prefix = managed_prefix
        self._run = run

    # --- the application ---

    def is_running(self) -> bool:
        return self._run(_IS_RUNNING, []) == "true"

    def launch(self, timeout: float = 30.0) -> None:
        """Start Music in the background if it is not running, and wait for it."""
        if self.is_running():
            return
        self._run(_LAUNCH, [])
        deadline = time.monotonic() + timeout
        while not self.is_running():
            if time.monotonic() >= deadline:
                raise MusicAppError(f"Music did not start within {timeout:.0f}s")
            time.sleep(0.5)

    def version(self) -> str:
        return self._run(_VERSION, [])

    def library_size(self) -> int:
        return int(self._run(_LIBRARY_SIZE, []))

    # --- reading ---

    def is_managed(self, name: str) -> bool:
        return name == self.managed_prefix or name.startswith(self.managed_prefix + " ")

    def playlists(self) -> list[MusicPlaylist]:
        """Every user playlist, folders and smart playlists included."""
        return self._playlists([])

    def find_playlist(self, name: str) -> MusicPlaylist | None:
        """The ordinary playlist with exactly this name, if there is one."""
        exact = [p for p in self._playlists([name]) if p.name == name and p.is_plain]
        if len(exact) > 1:
            raise MusicAppError(
                f"Music has {len(exact)} playlists named {name!r}; rename or delete the "
                "extras in Music so it is clear which one is meant"
            )
        return exact[0] if exact else None

    def playlist_tracks(self, name: str) -> list[AppleCandidate]:
        """Tracks of a playlist in the playlist's own order, however it is displayed."""
        playlist = self.find_playlist(name)
        if playlist is None:
            raise MusicAppError(f"Music has no playlist named {name!r}")
        return _tracks(self._run(_PLAYLIST_TRACKS, [playlist.persistent_id]))

    def search_songs(self, term: str, limit: int) -> list[AppleCandidate]:
        """Songs in the local library matching every word of `term`.

        Music matches each word as a prefix of a word in the song's details
        (title, artist, album, and others such as composer), in any order, ignoring
        case, accents and punctuation. One word that is absent sinks the match, so
        "and" is dropped: it is "&" as often as not.
        """
        words = [word for word in term.split() if word.casefold() != "and"]
        if not words:
            return []  # Music answers an empty search with error -50
        return _tracks(self._run(_SEARCH, [" ".join(words), str(limit)]))

    def has_track(self, persistent_id: str) -> bool:
        return self._run(_HAS_TRACK, [persistent_id]) == "true"

    def get_track(self, persistent_id: str) -> AppleCandidate | None:
        """The library track with this ID as it is now, or None if it is gone."""
        found = _tracks(self._run(_GET_TRACK, [persistent_id]))
        return found[0] if found else None

    # --- changing managed playlists ---

    def ensure_playlist(self, name: str) -> MusicPlaylist:
        """Return the managed playlist `name`, creating it if it does not exist."""
        self._require_managed(name)
        existing = self.find_playlist(name)
        if existing is not None:
            return existing
        self._run(_CREATE_PLAYLIST, [name])
        created = self.find_playlist(name)
        if created is None:
            raise MusicAppError(f"Music reported creating {name!r} but it cannot be found")
        return created

    def add_tracks(self, name: str, persistent_ids: Sequence[str]) -> list[str]:
        """Append library tracks to a managed playlist, in the order given.

        Returns the IDs that are no longer in the library; those are skipped.
        """
        if not persistent_ids:
            return []
        reply = self._run(_ADD_TRACKS, [*self._target(name), *persistent_ids])
        return reply.split(_RS) if reply else []

    def remove_track(self, name: str, persistent_id: str) -> int:
        """Take a track out of a managed playlist. Returns how many entries went.

        The track stays in the library.
        """
        return int(self._run(_REMOVE_TRACK, [*self._target(name), persistent_id]))

    def clear_playlist(self, name: str) -> int:
        """Empty a managed playlist. Returns how many entries were removed."""
        return int(self._run(_CLEAR_PLAYLIST, self._target(name)))

    def delete_playlist(self, name: str) -> bool:
        """Delete a managed playlist. Returns False if there was none to delete.

        Not used when syncing, which empties instead. With Sync Library on, a
        playlist deleted and soon created again under the same name has come back
        from iCloud as a second playlist of that name.
        """
        self._require_managed(name)
        playlist = self.find_playlist(name)
        if playlist is None:
            return False
        self._run(_DELETE_PLAYLIST, [playlist.persistent_id, name, self.managed_prefix])
        return True

    # --- internals ---

    def _playlists(self, args: list[str]) -> list[MusicPlaylist]:
        return [
            MusicPlaylist(
                persistent_id=persistent_id,
                name=name,
                kind=kind,
                special_kind=special_kind,
                smart=smart == "true",
                track_count=int(count),
            )
            for persistent_id, name, kind, special_kind, smart, count in _rows(
                self._run(_PLAYLISTS, args), 6
            )
        ]

    def _require_managed(self, name: str) -> None:
        if not self.is_managed(name):
            raise UnmanagedPlaylistError(
                f"refusing to change playlist {name!r}: only {self.managed_prefix!r} and "
                f"playlists starting with {self.managed_prefix + ' '!r} may be changed"
            )

    def _target(self, name: str) -> list[str]:
        """Script arguments identifying an existing managed playlist."""
        self._require_managed(name)
        playlist = self.find_playlist(name)
        if playlist is None:
            raise MusicAppError(f"Music has no playlist named {name!r}")
        return [playlist.persistent_id, name, self.managed_prefix]
