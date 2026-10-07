"""The whole sync path against the real Music app: export file -> library -> playlist.

Skipped unless pytest is run with --music-app. The export is named "Daily Mix TEST",
so the only playlist written is "Spotify Daily Mix TEST". The mapping database is a
temporary file, so the real cache is not touched either.
"""

import json

import pytest

from daily_mix_sync import cli

pytestmark = pytest.mark.music_app

TEST = "Spotify Daily Mix TEST"
MISSING = {"title": "Zzqqxx Notarealsongtitle", "artist": "Nobody At All"}


def as_entry(song, number):
    return {
        "title": song.title,
        "artist": song.artist,
        "album": song.album,
        "duration_ms": song.duration_ms,
        "spotify_track_id": f"LIVETEST{number:014d}",
    }


@pytest.fixture
def sync(tmp_path, capsys, monkeypatch):
    """Run the sync command on an export built from the given entries."""
    monkeypatch.chdir(tmp_path)  # no config.json here, so the defaults apply

    def run(entries, *options, name="Daily Mix TEST"):
        export = tmp_path / "daily_mix_test.json"
        export.write_text(json.dumps({"playlist_name": name, "tracks": entries}), encoding="utf-8")
        code = cli.main(["sync", str(export), "--db", str(tmp_path / "map.sqlite3"), *options])
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return run


def in_playlist(music):
    return [(t.title, t.artist, t.album) for t in music.playlist_tracks(TEST)]


def described(songs):
    return [(s.title, s.artist, s.album) for s in songs]


def test_dry_run_changes_nothing(music, songs, sync):
    before = music.playlist_tracks(TEST)
    entries = [as_entry(song, n) for n, song in enumerate(songs)]
    code, out, _ = sync(entries, "--dry-run")
    assert code == 0
    assert "Destination:      Spotify Daily Mix TEST" in out
    assert "No changes made (--dry-run)." in out
    assert music.playlist_tracks(TEST) == before


def test_sync_writes_library_songs_in_order_and_skips_what_is_missing(music, songs, sync):
    first, second, third = songs
    entries = [as_entry(third, 3), MISSING, as_entry(first, 1), as_entry(second, 2)]
    code, out, err = sync(entries, "--yes")
    assert (code, err) == (0, "")
    assert "Not in library:      1" in out
    assert "Zzqqxx Notarealsongtitle — Nobody At All" in out
    assert "Verified:           3 / 3, in order" in out
    assert "✓ Playlist updated and verified." in out
    assert in_playlist(music) == described([third, first, second])


def test_second_sync_reuses_the_cache_and_replaces_the_contents(music, songs, sync):
    first, second, third = songs
    sync([as_entry(third, 3), as_entry(first, 1), as_entry(second, 2)], "--yes")
    playlist_id = music.find_playlist(TEST).persistent_id

    # The same three source tracks, in a new order, one of them twice removed.
    code, out, _ = sync([as_entry(second, 2), as_entry(third, 3)], "--yes")
    assert code == 0
    assert "Cached matches:      2" in out and "New matches:         0" in out
    assert "Cleared old tracks: 3" in out and "Added new tracks:   2" in out
    assert in_playlist(music) == described([second, third])
    assert music.find_playlist(TEST).persistent_id == playlist_id  # emptied, not recreated


def test_sync_without_confirmation_leaves_the_playlist_alone(music, songs, sync, monkeypatch):
    monkeypatch.setattr(cli, "_interactive", lambda: False)
    before = music.playlist_tracks(TEST)
    code, _, err = sync([as_entry(songs[0], 1)])
    assert code == 1 and "not confirmed" in err
    assert music.playlist_tracks(TEST) == before


def test_sync_with_nothing_in_the_library_leaves_the_playlist_alone(music, sync):
    before = music.playlist_tracks(TEST)
    code, _, err = sync([MISSING], "--yes")
    assert code == 1 and "none of the 1 track(s) could be matched" in err
    assert music.playlist_tracks(TEST) == before


def test_sync_refuses_a_destination_outside_the_managed_playlists(music, songs, sync):
    unmanaged = next(
        (p for p in music.playlists() if p.is_plain and not music.is_managed(p.name)), None
    )
    if unmanaged is None:
        pytest.skip("no ordinary unmanaged playlist to test the refusal with")
    code, _, err = sync([as_entry(songs[0], 1)], "--yes", "--into", unmanaged.name)
    assert code == 1 and "refusing to write to playlist" in err
    assert music.find_playlist(unmanaged.name).track_count == unmanaged.track_count
