import json
import logging

import pytest

from daily_mix_sync.importer import InputError, load_playlist


def write(tmp_path, payload, name="daily_mix_1.json"):
    path = tmp_path / name
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
    return path


def test_loads_the_sample_export(sample_playlist_path):
    playlist = load_playlist(sample_playlist_path)
    assert playlist.name == "Daily Mix 1 (sample)"
    assert len(playlist.tracks) == 11
    assert playlist.skipped == ()
    assert playlist.duplicates == 0
    assert {t.playlist_name for t in playlist.tracks} == {"Daily Mix 1 (sample)"}

    by_title = {t.title: t for t in playlist.tracks}
    assert by_title["Mr. Brightside"].duration_ms == 222000
    assert by_title["Everlong - Acoustic Version"].spotify_track_id == "SAMPLE0000000000000006"
    assert by_title["Killing In The Name"].spotify_track_id is None
    assert by_title["Dreams"].album == ""
    assert by_title["Dreams"].duration_ms is None


@pytest.mark.parametrize(
    "url",
    [
        "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC?si=abc",
        "https://open.spotify.com/intl-de/track/4uLU6hMCjMI75M1A2tKUQC",
        "spotify:track:4uLU6hMCjMI75M1A2tKUQC",
    ],
)
def test_track_id_is_taken_from_the_url(tmp_path, url):
    path = write(tmp_path, {"tracks": [{"title": "T", "artist": "A", "spotify_url": url}]})
    assert load_playlist(path).tracks[0].spotify_track_id == "4uLU6hMCjMI75M1A2tKUQC"


def test_missing_file(tmp_path):
    with pytest.raises(InputError, match="cannot read"):
        load_playlist(tmp_path / "nope.json")


def test_invalid_json_says_where(tmp_path):
    path = write(tmp_path, '{"tracks": [')
    with pytest.raises(InputError, match=r"not valid JSON.*line 1"):
        load_playlist(path)


@pytest.mark.parametrize(
    "payload", [[], {"playlist_name": "X"}, {"tracks": "nope"}, '"a JSON string"', 3]
)
def test_wrong_shape(tmp_path, payload):
    with pytest.raises(InputError, match="expected an object like"):
        load_playlist(write(tmp_path, payload))


def test_empty_track_list(tmp_path):
    with pytest.raises(InputError, match="tracks list is empty"):
        load_playlist(write(tmp_path, {"tracks": []}))


def test_playlist_name_falls_back_to_the_file_name(tmp_path, caplog):
    path = write(tmp_path, {"tracks": [{"title": "T", "artist": "A"}]}, name="daily_mix_3.json")
    assert load_playlist(path).name == "daily_mix_3"
    assert "no playlist_name" in caplog.text


def test_entries_missing_required_metadata_are_reported_not_fatal(tmp_path):
    path = write(
        tmp_path,
        {
            "playlist_name": "Mix",
            "tracks": [
                {"title": "Good", "artist": "A"},
                {"title": "No Artist"},
                {"artist": "No Title", "album": "X"},
                {"title": "   ", "artist": None},
                "not an object",
                {"title": 1979, "artist": "Number Title"},
            ],
        },
    )
    playlist = load_playlist(path)
    assert [t.title for t in playlist.tracks] == ["Good"]
    assert playlist.skipped == (
        "track #2 ('No Artist'): missing artist",
        "track #3 ('No Title'): missing title",
        "track #4: missing title and artist",
        "track #5: expected an object, found str",
        "track #6 ('Number Title'): missing title",
    )


def test_optional_metadata_may_be_absent(tmp_path):
    track = load_playlist(write(tmp_path, {"tracks": [{"title": "T", "artist": "A"}]})).tracks[0]
    assert (track.album, track.duration_ms, track.spotify_track_id, track.spotify_url) == (
        "", None, None, None,
    )


@pytest.mark.parametrize("bad_duration", ["3:52", -5, 0, True, [1]])
def test_invalid_duration_is_dropped_but_the_track_is_kept(tmp_path, caplog, bad_duration):
    path = write(tmp_path, {"tracks": [{"title": "T", "artist": "A", "duration_ms": bad_duration}]})
    with caplog.at_level(logging.WARNING):
        track = load_playlist(path).tracks[0]
    assert track.duration_ms is None
    assert "ignoring invalid duration_ms" in caplog.text


def test_duplicate_tracks_are_collapsed(tmp_path):
    path = write(
        tmp_path,
        {
            "playlist_name": "Mix",
            "tracks": [
                {"title": "One", "artist": "A", "spotify_track_id": "id1"},
                {"title": "Two", "artist": "A", "album": "Al", "duration_ms": 200000},
                {"title": "One (again)", "artist": "A", "spotify_track_id": "id1"},
                {"title": "TWO", "artist": "a", "album": "Al", "duration_ms": 200400},
                {"title": "Three", "artist": "A"},
            ],
        },
    )
    playlist = load_playlist(path)
    assert [t.title for t in playlist.tracks] == ["One", "Two", "Three"]
    assert playlist.duplicates == 2
