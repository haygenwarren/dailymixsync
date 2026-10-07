"""An in-memory stand-in for osascript + Music, for unit tests.

It takes the place of music_app.run_osascript: it receives the same (script,
arguments) pairs and answers in the same wire format, so the real MusicApp code
(argument building, reply parsing, safety checks) runs unchanged. It imitates the
behaviours observed in the real app that the adapter has to cope with:

- playlist names are matched without regard to case
- a search needs every word to match, as a word prefix, in title, artist or album
- the scripts themselves refuse to change a playlist that is not managed
"""

from __future__ import annotations

from dataclasses import dataclass, field

from daily_mix_sync import music_app
from daily_mix_sync.models import AppleCandidate
from daily_mix_sync.normalize import normalize_text

US, RS = "\x1f", "\x1e"


@dataclass
class FakePlaylist:
    persistent_id: str
    name: str
    track_ids: list[str] = field(default_factory=list)
    kind: str = "user playlist"
    special_kind: str = "none"
    smart: bool = False


def _script_error(message: str, code: int) -> music_app.MusicAppError:
    return music_app.error_from_osascript(f"12:34: execution error: {message} ({code})")


class FakeMusic:
    def __init__(self, library=(), playlists=(), running=True, version="1.6.3"):
        self.library: dict[str, AppleCandidate] = {t.persistent_id: t for t in library}
        self.playlists: list[FakePlaylist] = list(playlists)
        self.running = running
        self.version = version
        self.calls: list[tuple[str, list[str]]] = []
        self.ignore_adds = False  # simulate Music accepting a request and doing nothing
        # {script: [n, ...]}: fail the n-th time (1-based) that script is sent
        self.fail_on: dict[str, list[int]] = {}
        self._sent: dict[str, int] = {}
        self._next_id = 1
        self._handlers = {
            music_app._IS_RUNNING: self._is_running,
            music_app._LAUNCH: self._launch,
            music_app._VERSION: lambda: self.version,
            music_app._LIBRARY_SIZE: lambda: str(len(self.library)),
            music_app._PLAYLISTS: self._playlists,
            music_app._PLAYLIST_TRACKS: self._playlist_tracks,
            music_app._SEARCH: self._search,
            music_app._HAS_TRACK: lambda track_id: str(track_id in self.library).lower(),
            music_app._GET_TRACK: self._get_track,
            music_app._CREATE_PLAYLIST: self._create,
            music_app._ADD_TRACKS: self._add,
            music_app._REMOVE_TRACK: self._remove,
            music_app._CLEAR_PLAYLIST: self._clear,
            music_app._DELETE_PLAYLIST: self._delete,
        }

    def __call__(self, script: str, args) -> str:
        self.calls.append((script, list(args)))
        self._sent[script] = self._sent.get(script, 0) + 1
        if self._sent[script] in self.fail_on.get(script, ()):
            raise _script_error("Music got an error: Connection is invalid.", -609)
        return self._handlers[script](*args)

    def changes(self) -> list[tuple[str, list[str]]]:
        """The calls that create or change something, in order."""
        changing = (
            music_app._CREATE_PLAYLIST, music_app._ADD_TRACKS, music_app._REMOVE_TRACK,
            music_app._CLEAR_PLAYLIST, music_app._DELETE_PLAYLIST,
        )
        return [(script, args) for script, args in self.calls if script in changing]

    def scripts_sent(self) -> list[str]:
        return [script for script, _ in self.calls]

    def playlist(self, name: str) -> FakePlaylist:
        return next(p for p in self.playlists if p.name == name)

    # --- script behaviour ---

    def _is_running(self) -> str:
        return str(self.running).lower()

    def _launch(self) -> str:
        self.running = True
        return ""

    def _track_rows(self, track_ids) -> str:
        rows = []
        for track_id in track_ids:
            t = self.library[track_id]
            ms = "" if t.duration_ms is None else str(t.duration_ms)
            rows.append(US.join([t.persistent_id, t.title, t.artist, t.album, ms]))
        return RS.join(rows)

    def _get_track(self, track_id: str) -> str:
        return self._track_rows([track_id]) if track_id in self.library else ""

    def _playlists(self, name: str | None = None) -> str:
        found = [p for p in self.playlists if name is None or p.name.lower() == name.lower()]
        return RS.join(
            US.join(
                [p.persistent_id, p.name, p.kind, p.special_kind, str(p.smart).lower(),
                 str(len(p.track_ids))]
            )
            for p in found
        )

    def _by_id(self, playlist_id: str) -> FakePlaylist:
        for p in self.playlists:
            if p.persistent_id == playlist_id:
                return p
        raise _script_error("the playlist no longer exists", 9002)

    def _managed(self, playlist_id: str, expected_name: str, prefix: str) -> FakePlaylist:
        p = self._by_id(playlist_id)
        if p.name != expected_name:
            raise _script_error(f"expected {expected_name} but found {p.name}", 9001)
        if not (p.name == prefix or p.name.startswith(prefix + " ")):
            raise _script_error(f"{p.name} is not a managed playlist", 9001)
        if p.kind != "user playlist" or p.smart or p.special_kind != "none":
            raise _script_error(f"{p.name} is not an ordinary playlist", 9001)
        return p

    def _playlist_tracks(self, playlist_id: str) -> str:
        return self._track_rows(self._by_id(playlist_id).track_ids)

    def _search(self, term: str, limit: str) -> str:
        if not term.strip():
            raise _script_error("Music got an error: Parameter error.", -50)
        wanted = normalize_text(term).split()
        hits = []
        for t in self.library.values():
            words = normalize_text(f"{t.title} {t.artist} {t.album}").split()
            if all(any(word.startswith(w) for word in words) for w in wanted):
                hits.append(t.persistent_id)
        return self._track_rows(hits[: int(limit)])

    def _create(self, name: str) -> str:
        playlist = FakePlaylist(f"PL{self._next_id:014d}", name)
        self._next_id += 1
        self.playlists.append(playlist)
        return playlist.persistent_id

    def _add(self, playlist_id: str, expected_name: str, prefix: str, *track_ids: str) -> str:
        p = self._managed(playlist_id, expected_name, prefix)
        missing = [t for t in track_ids if t not in self.library]
        if not self.ignore_adds:
            p.track_ids += [t for t in track_ids if t in self.library]
        return RS.join(missing)

    def _remove(self, playlist_id: str, expected_name: str, prefix: str, track_id: str) -> str:
        p = self._managed(playlist_id, expected_name, prefix)
        removed = p.track_ids.count(track_id)
        p.track_ids = [t for t in p.track_ids if t != track_id]
        return str(removed)

    def _clear(self, playlist_id: str, expected_name: str, prefix: str) -> str:
        p = self._managed(playlist_id, expected_name, prefix)
        removed = len(p.track_ids)
        p.track_ids = []
        return str(removed)

    def _delete(self, playlist_id: str, expected_name: str, prefix: str) -> str:
        self.playlists.remove(self._managed(playlist_id, expected_name, prefix))
        return ""
