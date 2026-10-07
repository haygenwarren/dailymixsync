"""A stand-in for the Music window, for unit tests.

It plays the part of music_ui.MusicCatalogUI: it answers catalog searches from a
fixed list of songs and, when told to add one, puts a track into the fake Music
library, at once or after a delay. Nothing here touches the real GUI.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass

from fake_music import FakeMusic

from daily_mix_sync.models import AppleCandidate
from daily_mix_sync.music_app import ACCESSIBILITY_HELP, AccessibilityPermissionError
from daily_mix_sync.music_ui import CatalogUIResult
from daily_mix_sync.normalize import normalize_text


@dataclass(frozen=True)
class CatalogSong:
    """A song in the pretend Apple Music catalog, with the metadata the library will show."""

    catalog_id: str
    title: str
    artist: str
    album: str = ""
    duration_ms: int | None = None

    def as_library_track(self) -> AppleCandidate:
        return AppleCandidate(f"LIB{self.catalog_id}", self.title, self.artist, self.album, self.duration_ms)


class FakeCatalogUI:
    def __init__(self, music: FakeMusic, songs: list[CatalogSong], allowed: bool = True) -> None:
        self.music = music
        self.songs = list(songs)
        self.allowed = allowed
        self.searches: list[str] = []
        self.added: list[CatalogUIResult] = []
        self.sessions = 0
        self.handed_back = 0
        self.appear_after = 0  # library searches before an added song becomes visible
        self.clicks_register = True  # False: Add to Library is chosen but nothing happens
        self.fail_search: dict[str, Exception] = {}  # search term -> what to raise
        self.fail_add: Exception | None = None
        self.arrives_as: dict[str, AppleCandidate] = {}  # catalog id -> what the library gets
        self.in_library_ids: set[str] = set()  # catalog ids Music shows as already added
        self.layout = [  # what inspect() reports
            ("ok", "main window", "Music, 1 window(s) in all"),
            ("ok", "search field", 'holds "", placeholder "Apple Music"'),
            ("info", "Songs section", "not present (it appears after a search)"),
        ]

    # --- the interface of MusicCatalogUI ---

    def accessibility_allowed(self) -> bool:
        return self.allowed

    def require_accessibility(self) -> None:
        if not self.allowed:
            raise AccessibilityPermissionError(
                "Searching the Apple Music catalog needs macOS Accessibility permission. "
                + ACCESSIBILITY_HELP
            )

    @contextmanager
    def session(self):
        self.sessions += 1
        try:
            yield
        finally:
            self.handed_back += 1

    def hand_back(self) -> None:
        self.handed_back += 1

    def search_catalog(self, term: str) -> list[CatalogUIResult]:
        self.searches.append(term)
        if term in self.fail_search:
            raise self.fail_search[term]
        wanted = set(normalize_text(term).split())
        rows = []
        # Like the real search, generous: anything sharing half the words comes back.
        for song in self.songs:
            words = set(normalize_text(f"{song.title} {song.artist}").split())
            if wanted and 2 * len(wanted & words) >= len(wanted):
                rows.append(
                    CatalogUIResult(
                        title=song.title,
                        artist=song.artist,
                        ordinal=len(rows) + 1,
                        element_id=(
                            "Music.shelfItem.TrackLockup[id=track-section-song-"
                            f"{song.catalog_id},parentId=track-section-song]"
                        ),
                    )
                )
        return rows

    def inspect(self) -> list[tuple[str, str, str]]:
        return list(self.layout)

    def dump(self, part: str) -> str:
        tail = " of window Music of application process Music"
        return f"group 1 of {part}{tail}, button Search of group 1 of {part}{tail}"

    def in_library(self, result: CatalogUIResult) -> bool:
        return result.catalog_id in self.in_library_ids

    def add_to_library(self, result: CatalogUIResult) -> bool:
        if self.fail_add is not None:
            raise self.fail_add
        if result.catalog_id in self.in_library_ids:
            return False
        self.added.append(result)
        if self.clicks_register:
            self.in_library_ids.add(result.catalog_id)
            song = next(s for s in self.songs if s.catalog_id == result.catalog_id)
            track = self.arrives_as.get(song.catalog_id, song.as_library_track())
            self.music.add_to_library(track, after_searches=self.appear_after)
        return True
