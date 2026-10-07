"""Fixtures for the tests that drive the real Music app.

A test file that asks for the `test_playlist` fixture gets an empty
"Spotify Daily Mix TEST" playlist to work in. When the file is done, the playlist's
contents are put back exactly as they were found, and the library and every other
playlist are checked to be unchanged.

The playlist itself is never deleted, not even when these tests created it: with
Sync Library on, a playlist deleted and then created again under the same name has
come back from iCloud as a duplicate. A playlist the tests had to create is left in
place, empty.
"""

import pytest

from daily_mix_sync.config import Settings
from daily_mix_sync.matcher import MatchStatus
from daily_mix_sync.models import SourceTrack
from daily_mix_sync.music_app import MusicApp
from daily_mix_sync.sync import match_one

TEST = "Spotify Daily Mix TEST"


@pytest.fixture(scope="module")
def music():
    return MusicApp()


@pytest.fixture(scope="module")
def songs(music):
    """Three different library songs that the matcher finds again from their own metadata."""
    found = {}
    for word in ("the", "love", "you", "a", "me", "in"):
        for song in music.search_songs(word, 5):
            source = SourceTrack(song.title, song.artist, song.album, song.duration_ms)
            result = match_one(source, music, Settings())
            same = result.chosen is not None and result.chosen.persistent_id == song.persistent_id
            if result.status is MatchStatus.MATCHED and same:
                found.setdefault((song.title, song.artist), song)
        if len(found) >= 3:
            return list(found.values())[:3]
    pytest.skip("the Music library has fewer than three songs to test with")


@pytest.fixture(scope="module")
def baseline(music):
    """What must be the same after the tests as before them."""

    def snapshot():
        return {
            "library size": music.library_size(),
            "other playlists": {
                (p.persistent_id, p.name, p.track_count)
                for p in music.playlists()
                if p.name != TEST
            },
        }

    before = snapshot()
    yield before
    assert snapshot() == before, "something other than the test playlist changed"


@pytest.fixture(scope="module")
def test_playlist(music, baseline):
    """Hand the tests an empty test playlist, then put back whatever it held."""
    existed = music.find_playlist(TEST) is not None
    previous = [t.persistent_id for t in music.playlist_tracks(TEST)] if existed else []
    playlist_id = music.ensure_playlist(TEST).persistent_id
    music.clear_playlist(TEST)
    yield
    assert music.find_playlist(TEST).persistent_id == playlist_id, "the test playlist was replaced"
    music.clear_playlist(TEST)
    music.add_tracks(TEST, previous)
    restored = [t.persistent_id for t in music.playlist_tracks(TEST)]
    assert restored == previous, "the test playlist could not be put back as it was"
