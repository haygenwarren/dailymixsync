"""The browser extension's output, read by the Python importer.

tests/extension/fixtures/expected_export.json is the file the extension must produce
for its fixture page; the JavaScript tests compare the extension's output with it byte
for byte. Loading the same file here ties the two halves together: what the extension
writes is what the application reads, with nothing skipped and nothing to edit by hand.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from daily_mix_sync.importer import load_playlist
from daily_mix_sync.normalize import source_key
from daily_mix_sync.sync import destination_name

EXPORT = Path(__file__).parent / "extension" / "fixtures" / "expected_export.json"


@pytest.fixture(scope="module")
def raw() -> dict:
    return json.loads(EXPORT.read_text(encoding="utf-8"))


def test_the_export_loads_with_every_track(raw, caplog):
    with caplog.at_level(logging.INFO):
        playlist = load_playlist(EXPORT)
    assert playlist.name == "Daily Mix 1"
    assert len(playlist.tracks) == len(raw["tracks"]) == 11
    assert playlist.skipped == ()
    assert playlist.duplicates == 0
    assert caplog.records == [], "nothing to warn about, nothing dropped"


def test_every_field_arrives_as_written(raw):
    playlist = load_playlist(EXPORT)
    for entry, track in zip(raw["tracks"], playlist.tracks, strict=True):
        assert track.title == entry["title"]
        assert track.artist == entry["artist"]
        assert track.album == entry.get("album", "")
        assert track.duration_ms == entry.get("duration_ms")
        assert track.spotify_track_id == entry["spotify_track_id"]
        assert track.spotify_url == entry["spotify_url"]
        assert track.playlist_name == "Daily Mix 1"


def test_durations_are_whole_milliseconds(raw):
    for entry in raw["tracks"]:
        if "duration_ms" in entry:
            assert type(entry["duration_ms"]) is int and entry["duration_ms"] > 0
    playlist = load_playlist(EXPORT)
    assert playlist.tracks[0].duration_ms == 222_000  # "3:42"
    assert playlist.tracks[7].duration_ms == 3_795_000  # "1:03:15"


def test_the_id_and_the_url_agree(raw):
    for entry in raw["tracks"]:
        assert entry["spotify_url"] == f"https://open.spotify.com/track/{entry['spotify_track_id']}"
    # The importer can also work the ID out from the URL alone; both give the same.
    for track in load_playlist(EXPORT).tracks:
        assert source_key(track) == f"spotify:track:{track.spotify_track_id}"


def test_a_track_without_album_or_duration_is_still_accepted():
    track = load_playlist(EXPORT).tracks[8]
    assert track.title == 'Rock & Roll <Live> "Bootleg"'
    assert track.album == ""
    assert track.duration_ms is None


def test_same_title_songs_stay_two_tracks():
    tracks = load_playlist(EXPORT).tracks
    brightsides = [track for track in tracks if track.title == "Mr. Brightside"]
    assert [track.artist for track in brightsides] == ["The Killers", "Run River North"]


def test_text_is_untouched_by_the_extension():
    tracks = load_playlist(EXPORT).tracks
    assert tracks[4].title == "Déjà Vu (feat. JAY-Z)"
    assert tracks[5].title == "夜に駆ける"
    assert tracks[6].artist == "Tyler, The Creator, Kali Uchis"
    assert tracks[7].title == "Autobahn - 2009 Remaster"


def test_extra_top_level_fields_are_ignored(raw):
    assert {"source_url", "exported_at"} <= raw.keys()
    assert load_playlist(EXPORT).name == raw["playlist_name"]


def test_the_playlist_name_gives_the_managed_destination():
    playlist = load_playlist(EXPORT)
    assert destination_name(playlist.name, "Spotify Daily Mix") == "Spotify Daily Mix 1"


def test_the_export_is_utf8_json_ending_in_a_newline():
    text = EXPORT.read_bytes().decode("utf-8")
    assert text.endswith("}\n")
    assert "\\u" not in text, "written as readable text, not escape codes"
