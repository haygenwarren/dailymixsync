"""Replacing a playlist's contents: order, verification, and putting things back."""

import pytest
from fake_music import FakeMusic, FakePlaylist

from daily_mix_sync import music_app
from daily_mix_sync.models import AppleCandidate
from daily_mix_sync.music_app import MusicApp, MusicAppError, UnmanagedPlaylistError
from daily_mix_sync.sync import PlaylistWriteError, destination_name, write_playlist

SONGS = [AppleCandidate(f"T{n}", f"Song {n}", "Artist", "Album", 200_000 + n) for n in range(1, 8)]
DEST = "Spotify Daily Mix 1"
LIKED = "Spotify Liked Songs"
OLD = ["T6", "T7"]  # what the destination holds before a sync


@pytest.fixture
def fake():
    return FakeMusic(
        library=SONGS,
        playlists=[
            FakePlaylist("LIKED00000000001", LIKED, ["T1", "T2", "T3"]),
            FakePlaylist("DEST000000000001", DEST, list(OLD)),
        ],
    )


@pytest.fixture
def music(fake):
    return MusicApp(run=fake)


def change_kinds(fake):
    names = {
        music_app._CREATE_PLAYLIST: "create",
        music_app._ADD_TRACKS: "add",
        music_app._REMOVE_TRACK: "remove",
        music_app._CLEAR_PLAYLIST: "clear",
        music_app._DELETE_PLAYLIST: "delete",
    }
    return [names[script] for script, _ in fake.changes()]


# --- where a playlist goes ------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "destination"),
    [
        ("Daily Mix 1", "Spotify Daily Mix 1"),
        ("Daily Mix 6", "Spotify Daily Mix 6"),
        ("Daily Mix TEST", "Spotify Daily Mix TEST"),
        ("daily mix 2", "Spotify Daily Mix 2"),
        ("  Daily   Mix  3 ", "Spotify Daily Mix 3"),
        ("Spotify Daily Mix 4", "Spotify Daily Mix 4"),
        ("Daily Mix 1 (sample)", "Spotify Daily Mix 1 (sample)"),
        ("Daily Mix", "Spotify Daily Mix"),
        ("Mix 5", "Spotify Daily Mix 5"),
        ("Discover Weekly", "Spotify Daily Mix Discover Weekly"),
        ("Daily Mixtape 1", "Spotify Daily Mix Daily Mixtape 1"),
        ("Spotify Liked Songs", "Spotify Daily Mix Spotify Liked Songs"),
    ],
)
def test_destination_name(source, destination):
    assert destination_name(source, "Spotify Daily Mix") == destination


def test_destination_follows_the_configured_prefix():
    assert destination_name("Daily Mix 1", "Mirror") == "Mirror Daily Mix 1"
    assert destination_name("Daily Mix 1", "From Spotify: Daily Mix") == "From Spotify: Daily Mix 1"


@pytest.mark.parametrize(
    "source",
    ["Spotify Liked Songs", "Road Trip", "Spotify", "Spotify Daily", "Spotify Daily Mixtape",
     "../../etc", "Favorite Songs", "Music", "", "   ", "Liked Songs\nSpotify Daily Mix 1"],
)
def test_no_source_name_can_point_at_an_unmanaged_playlist(music, source):
    assert music.is_managed(destination_name(source, music.managed_prefix))


# --- the normal case ------------------------------------------------------------


def test_write_empties_then_adds_in_order_and_checks(music, fake):
    report = write_playlist(music, DEST, ["T3", "T1", "T2"])
    assert (report.destination, report.created) == (DEST, False)
    assert (report.previous_count, report.written) == (2, 3)
    assert fake.playlist(DEST).track_ids == ["T3", "T1", "T2"]
    assert change_kinds(fake) == ["clear", "add"]
    assert fake.playlist(DEST).persistent_id == "DEST000000000001"  # emptied, not recreated
    assert fake.playlist(LIKED).track_ids == ["T1", "T2", "T3"]


def test_write_creates_a_missing_playlist(music, fake):
    report = write_playlist(music, "Spotify Daily Mix 2", ["T1", "T2"])
    assert report.created and report.previous_count == 0 and report.written == 2
    assert fake.playlist("Spotify Daily Mix 2").track_ids == ["T1", "T2"]
    assert change_kinds(fake) == ["create", "clear", "add"]


def test_the_same_track_twice_is_written_twice(music, fake):
    write_playlist(music, DEST, ["T1", "T2", "T1"])
    assert fake.playlist(DEST).track_ids == ["T1", "T2", "T1"]


def test_every_change_names_only_the_destination(music, fake):
    write_playlist(music, DEST, ["T1"])
    assert {args[1] for _, args in fake.changes()} == {DEST}


# --- refusals before anything is touched ------------------------------------------


@pytest.mark.parametrize("name", [LIKED, "Road Trip", "spotify daily mix 1", "Spotify Daily Mixtape"])
def test_unmanaged_destination_is_refused_untouched(music, fake, name):
    with pytest.raises(UnmanagedPlaylistError):
        write_playlist(music, name, ["T4"])
    assert fake.changes() == []
    assert fake.playlist(LIKED).track_ids == ["T1", "T2", "T3"]


def test_failure_before_the_playlist_is_touched_changes_nothing(music, fake):
    fake.fail_on[music_app._PLAYLIST_TRACKS] = [1]  # reading the current contents fails
    with pytest.raises(MusicAppError):
        write_playlist(music, DEST, ["T1"])
    assert fake.changes() == []
    assert fake.playlist(DEST).track_ids == OLD


# --- verification ---------------------------------------------------------------


def test_too_few_tracks_after_writing_is_caught_and_rolled_back(music, fake):
    real_add = fake._handlers[music_app._ADD_TRACKS]
    calls = []

    def drops_one_the_first_time(playlist_id, name, prefix, *track_ids):
        calls.append(track_ids)
        return real_add(playlist_id, name, prefix, *(track_ids[:-1] if len(calls) == 1 else track_ids))

    fake._handlers[music_app._ADD_TRACKS] = drops_one_the_first_time
    with pytest.raises(PlaylistWriteError) as caught:
        write_playlist(music, DEST, ["T1", "T2", "T3"])
    assert str(caught.value) == "Expected 3 track(s); Music reports 2."
    assert caught.value.restored is True
    assert fake.playlist(DEST).track_ids == OLD


def test_wrong_order_after_writing_is_caught(music, fake):
    real_add = fake._handlers[music_app._ADD_TRACKS]
    calls = []

    def reversed_the_first_time(playlist_id, name, prefix, *track_ids):
        calls.append(track_ids)
        ordered = tuple(reversed(track_ids)) if len(calls) == 1 else track_ids
        return real_add(playlist_id, name, prefix, *ordered)

    fake._handlers[music_app._ADD_TRACKS] = reversed_the_first_time
    with pytest.raises(PlaylistWriteError) as caught:
        write_playlist(music, DEST, ["T1", "T2", "T3"])
    assert str(caught.value) == "Music reports the right 3 track(s), but in a different order."
    assert caught.value.restored and fake.playlist(DEST).track_ids == OLD


def test_wrong_tracks_after_writing_are_caught(music, fake):
    real_add = fake._handlers[music_app._ADD_TRACKS]
    calls = []

    def swaps_one_the_first_time(playlist_id, name, prefix, *track_ids):
        calls.append(track_ids)
        wanted = ("T5", *track_ids[1:]) if len(calls) == 1 else track_ids
        return real_add(playlist_id, name, prefix, *wanted)

    fake._handlers[music_app._ADD_TRACKS] = swaps_one_the_first_time
    with pytest.raises(PlaylistWriteError) as caught:
        write_playlist(music, DEST, ["T1", "T2", "T3"])
    assert str(caught.value) == "1 of the 3 track(s) in the playlist are not the intended ones."
    assert caught.value.restored and fake.playlist(DEST).track_ids == OLD


def test_music_doing_nothing_is_not_mistaken_for_success(music, fake):
    fake.ignore_adds = True
    with pytest.raises(PlaylistWriteError) as caught:
        write_playlist(music, DEST, ["T1", "T2"])
    assert str(caught.value) == "Expected 2 track(s); Music reports 0."
    # Restoring needs adds too, and those are still being ignored.
    assert caught.value.restored is False
    assert caught.value.restore_problem == "Expected 2 track(s); Music reports 0."
    assert [t.persistent_id for t in caught.value.previous] == OLD


def test_a_track_that_left_the_library_is_not_silently_dropped(music, fake):
    with pytest.raises(PlaylistWriteError) as caught:
        write_playlist(music, DEST, ["T1", "GONE", "T2"])
    assert str(caught.value) == "1 track(s) left the Music library before they could be added."
    assert caught.value.restored and fake.playlist(DEST).track_ids == OLD


# --- failures part-way and rollback -----------------------------------------------


def test_failure_while_adding_restores_the_previous_contents(music, fake):
    fake.fail_on[music_app._ADD_TRACKS] = [1]
    with pytest.raises(PlaylistWriteError) as caught:
        write_playlist(music, DEST, ["T1", "T2", "T3"])
    assert "Connection is invalid" in str(caught.value)
    assert caught.value.restored is True
    assert fake.playlist(DEST).track_ids == OLD  # same tracks, same order
    assert change_kinds(fake) == ["clear", "add", "clear", "add"]
    assert fake.playlist(LIKED).track_ids == ["T1", "T2", "T3"]


def test_failure_while_emptying_restores_too(music, fake):
    fake.fail_on[music_app._CLEAR_PLAYLIST] = [1]
    with pytest.raises(PlaylistWriteError) as caught:
        write_playlist(music, DEST, ["T1"])
    assert caught.value.restored and fake.playlist(DEST).track_ids == OLD


def test_a_newly_created_playlist_is_left_empty_when_its_first_write_fails(music, fake):
    fake.fail_on[music_app._ADD_TRACKS] = [1]
    with pytest.raises(PlaylistWriteError) as caught:
        write_playlist(music, "Spotify Daily Mix 2", ["T1"])
    assert caught.value.restored and caught.value.previous == []
    assert fake.playlist("Spotify Daily Mix 2").track_ids == []


def test_interruption_part_way_restores_the_previous_contents(music, fake):
    real_add = fake._handlers[music_app._ADD_TRACKS]
    calls = []

    def interrupted_the_first_time(*args):
        calls.append(args)
        if len(calls) == 1:
            raise KeyboardInterrupt
        return real_add(*args)

    fake._handlers[music_app._ADD_TRACKS] = interrupted_the_first_time
    with pytest.raises(PlaylistWriteError) as caught:
        write_playlist(music, DEST, ["T1", "T2"])
    assert str(caught.value) == "interrupted"
    assert caught.value.restored and fake.playlist(DEST).track_ids == OLD


def test_failed_rollback_is_reported_with_what_was_lost(music, fake):
    fake.fail_on[music_app._ADD_TRACKS] = [1, 2]  # the write, then the restore
    with pytest.raises(PlaylistWriteError) as caught:
        write_playlist(music, DEST, ["T1", "T2", "T3"])
    error = caught.value
    assert error.restored is False
    assert "Connection is invalid" in str(error)
    assert "Connection is invalid" in error.restore_problem
    assert [(t.persistent_id, t.title) for t in error.previous] == [("T6", "Song 6"), ("T7", "Song 7")]
    assert fake.playlist(LIKED).track_ids == ["T1", "T2", "T3"]


def test_rollback_that_music_only_half_performs_is_not_called_restored(music, fake):
    real_add = fake._handlers[music_app._ADD_TRACKS]
    calls = []

    def fails_then_restores_only_one(playlist_id, name, prefix, *track_ids):
        calls.append(track_ids)
        if len(calls) == 1:
            raise music_app.error_from_osascript("0:1: execution error: Music got an error: Boom. (-1)")
        return real_add(playlist_id, name, prefix, *track_ids[:1])

    fake._handlers[music_app._ADD_TRACKS] = fails_then_restores_only_one
    with pytest.raises(PlaylistWriteError) as caught:
        write_playlist(music, DEST, ["T1"])
    assert caught.value.restored is False
    assert caught.value.restore_problem == "Expected 2 track(s); Music reports 1."
