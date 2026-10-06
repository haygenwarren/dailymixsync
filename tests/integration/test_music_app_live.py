"""Tests that drive the real Music app on this Mac.

Skipped unless pytest is run with --music-app. They read the library and change
exactly one playlist, "Spotify Daily Mix TEST": it is created if missing, and at
the end it is put back the way it was found (or deleted if it did not exist).

They need Automation permission for whatever app runs pytest; see the README.
"""

import pytest

from daily_mix_sync import music_app
from daily_mix_sync.matcher import MatchStatus, match_track
from daily_mix_sync.models import SourceTrack
from daily_mix_sync.music_app import MusicApp, UnmanagedPlaylistError, run_osascript
from daily_mix_sync.sync import search_term

pytestmark = pytest.mark.music_app

TEST = "Spotify Daily Mix TEST"


@pytest.fixture(scope="module")
def music():
    return MusicApp()


@pytest.fixture(scope="module")
def songs(music):
    """Three different songs that are in the library."""
    found = {}
    for word in ("the", "love", "you", "a", "me", "in"):
        for song in music.search_songs(word, 5):
            found.setdefault(song.persistent_id, song)
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


@pytest.fixture(scope="module", autouse=True)
def test_playlist(music, baseline):
    """Hand the tests an empty test playlist, then restore whatever was there."""
    existed = music.find_playlist(TEST) is not None
    previous = [t.persistent_id for t in music.playlist_tracks(TEST)] if existed else []
    music.ensure_playlist(TEST)
    music.clear_playlist(TEST)
    yield
    if existed:
        music.ensure_playlist(TEST)
        music.clear_playlist(TEST)
        music.add_tracks(TEST, previous)
    else:
        music.delete_playlist(TEST)


def titles(music):
    return [t.title for t in music.playlist_tracks(TEST)]


def test_music_answers(music):
    assert music.is_running()
    assert music.version()
    assert music.library_size() > 0


def test_ensure_playlist_reuses_rather_than_duplicates(music):
    first = music.ensure_playlist(TEST)
    again = music.ensure_playlist(TEST)
    assert first.persistent_id == again.persistent_id
    assert first.is_plain
    assert sum(p.name == TEST for p in music.playlists()) == 1


def test_playlist_lookup_needs_the_exact_name(music):
    assert music.find_playlist(TEST.lower()) is None


def test_reading_an_empty_playlist(music):
    assert music.playlist_tracks(TEST) == []


def test_library_song_is_found_by_title_and_artist_with_the_existing_matcher(music, songs):
    wanted = songs[0]
    source = SourceTrack(wanted.title, wanted.artist, wanted.album, wanted.duration_ms)
    result = match_track(source, music.search_songs(search_term(source), 25))
    assert result.status is MatchStatus.MATCHED
    assert result.best.score == 100
    # Several library entries can be the same recording; the best one must be equivalent.
    best = result.best.candidate
    assert (best.title, best.artist) == (wanted.title, wanted.artist)
    assert music.has_track(best.persistent_id)


def test_search_for_nothing_and_for_nonsense(music):
    assert music.search_songs("", 5) == []
    assert music.search_songs("zzqqxx notarealsongtitle", 5) == []
    assert music.has_track("0000000000000000") is False


def test_add_keeps_order_and_identity(music, songs):
    ids = [s.persistent_id for s in songs]
    assert music.add_tracks(TEST, ids) == []
    assert music.playlist_tracks(TEST) == songs


def test_remove_one_track_leaves_the_rest_and_the_library(music, songs, baseline):
    first, second, third = songs
    assert music.remove_track(TEST, second.persistent_id) == 1
    assert music.playlist_tracks(TEST) == [first, third]
    assert music.remove_track(TEST, second.persistent_id) == 0
    assert music.has_track(second.persistent_id)
    assert music.library_size() == baseline["library size"]


def test_duplicates_are_possible_and_removed_together(music, songs):
    first, _, third = songs
    music.add_tracks(TEST, [first.persistent_id])
    assert music.playlist_tracks(TEST) == [first, third, first]
    assert music.remove_track(TEST, first.persistent_id) == 2
    assert music.playlist_tracks(TEST) == [third]


def test_unknown_track_ids_are_reported_and_skipped(music, songs):
    assert music.add_tracks(TEST, ["0000000000000000", songs[0].persistent_id]) == [
        "0000000000000000"
    ]
    assert titles(music) == [songs[2].title, songs[0].title]


def test_clear_keeps_the_playlist_and_the_library(music, songs, baseline):
    before = music.find_playlist(TEST)
    assert music.clear_playlist(TEST) == 2
    assert music.playlist_tracks(TEST) == []
    assert music.clear_playlist(TEST) == 0
    assert music.find_playlist(TEST).persistent_id == before.persistent_id
    assert music.library_size() == baseline["library size"]
    assert all(music.has_track(s.persistent_id) for s in songs)


def test_delete_and_recreate_gives_a_new_playlist(music, songs, baseline):
    old = music.find_playlist(TEST)
    music.add_tracks(TEST, [songs[0].persistent_id])
    assert music.delete_playlist(TEST) is True
    assert music.find_playlist(TEST) is None
    assert music.delete_playlist(TEST) is False
    assert music.library_size() == baseline["library size"]
    assert music.has_track(songs[0].persistent_id)
    new = music.ensure_playlist(TEST)
    assert new.persistent_id != old.persistent_id
    assert new.track_count == 0


@pytest.fixture(scope="module")
def someone_elses(music):
    """An ordinary playlist of the user's that the tool must never change."""
    for playlist in music.playlists():
        if playlist.is_plain and not music.is_managed(playlist.name):
            return playlist
    pytest.skip("no ordinary unmanaged playlist to test the refusal with")


def test_the_check_inside_the_scripts_refuses_other_playlists(someone_elses):
    """The AppleScript-side check, run on its own: this script changes nothing."""
    check_only = music_app._HELPERS + """
on run argv
	my managedPlaylist(item 1 of argv, item 2 of argv, item 3 of argv)
	return "accepted"
end run
"""
    real = someone_elses
    # Told the truth about the name: refused, because the name is not managed.
    with pytest.raises(UnmanagedPlaylistError, match="is not a managed playlist"):
        run_osascript(check_only, [real.persistent_id, real.name, "Spotify Daily Mix"])
    # Told a managed name for an unmanaged playlist: refused, because the names differ.
    with pytest.raises(UnmanagedPlaylistError, match="expected Spotify Daily Mix TEST but found"):
        run_osascript(check_only, [real.persistent_id, TEST, "Spotify Daily Mix"])
    # And it does accept the genuine test playlist.
    test_id = MusicApp().ensure_playlist(TEST).persistent_id
    assert run_osascript(check_only, [test_id, TEST, "Spotify Daily Mix"]) == "accepted"


def test_other_playlists_are_refused(music, someone_elses):
    for change in (music.clear_playlist, music.delete_playlist, music.ensure_playlist):
        with pytest.raises(UnmanagedPlaylistError):
            change(someone_elses.name)
    assert music.find_playlist(someone_elses.name).track_count == someone_elses.track_count
