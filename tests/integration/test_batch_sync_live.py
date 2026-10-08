"""Several exports in one sync, against the real Music app.

Skipped unless pytest is run with --music-app. Two playlists are written, and only
these two: "Spotify Daily Mix TEST 1" and "Spotify Daily Mix TEST 2". What they held
is recorded first and put back afterwards; they are never deleted, so if these tests
had to create them they are left in place, empty. Real Daily Mix playlists are never
touched: the exports here are named "Daily Mix TEST 1" and "Daily Mix TEST 2".

The mapping database is a temporary file, so the real cache is not touched either.
Only AppleScript is used, and nothing is added to the library.
"""

import json

import pytest

from daily_mix_sync import cli

pytestmark = pytest.mark.music_app

ONE, TWO = "Spotify Daily Mix TEST 1", "Spotify Daily Mix TEST 2"
MISSING = {"title": "Zzqqxx Notarealsongtitle", "artist": "Nobody At All"}


def as_entry(song, number):
    return {
        "title": song.title,
        "artist": song.artist,
        "album": song.album,
        "duration_ms": song.duration_ms,
        "spotify_track_id": f"LIVEBATCH{number:013d}",
    }


@pytest.fixture(scope="module", autouse=True)
def test_playlists(music):
    """Two empty test playlists for the tests; afterwards, exactly what was there before.

    Also checks that nothing else moved: the library is the same size, and every other
    playlist has the same identity, name and number of tracks.
    """

    def everything_else():
        return (
            music.library_size(),
            {(p.persistent_id, p.name, p.track_count) for p in music.playlists() if p.name not in (ONE, TWO)},
        )

    before = everything_else()
    previous, identities = {}, {}
    for name in (ONE, TWO):
        existed = music.find_playlist(name) is not None
        previous[name] = [t.persistent_id for t in music.playlist_tracks(name)] if existed else []
        identities[name] = music.ensure_playlist(name).persistent_id
        music.clear_playlist(name)
    yield
    for name in (ONE, TWO):
        assert music.find_playlist(name).persistent_id == identities[name], f"{name} was replaced"
        music.clear_playlist(name)
        music.add_tracks(name, previous[name])
        restored = [t.persistent_id for t in music.playlist_tracks(name)]
        assert restored == previous[name], f"{name} could not be put back as it was"
    assert everything_else() == before, "something other than the two test playlists changed"


@pytest.fixture
def sync(tmp_path, capsys, monkeypatch):
    """Run one sync over exports built from the given entries, one file per playlist name."""
    monkeypatch.chdir(tmp_path)  # no config.json here, so the defaults apply

    def run(mixes, *options):
        paths = []
        for number, (name, entries) in enumerate(mixes):
            path = tmp_path / f"export_{number}.json"
            path.write_text(json.dumps({"playlist_name": name, "tracks": entries}), encoding="utf-8")
            paths.append(str(path))
        code = cli.main(["sync", *paths, "--db", str(tmp_path / "map.sqlite3"), *options])
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return run


def held(music, name):
    return [(t.title, t.artist, t.album) for t in music.playlist_tracks(name)]


def described(songs):
    return [(s.title, s.artist, s.album) for s in songs]


def empty_both(music):
    for name in (ONE, TWO):
        music.clear_playlist(name)


def test_a_batch_dry_run_changes_nothing(music, songs, sync):
    empty_both(music)
    first, second, third = songs
    code, out, err = sync(
        [("Daily Mix TEST 1", [as_entry(first, 1), as_entry(second, 2)]),
         ("Daily Mix TEST 2", [as_entry(third, 3)])],
        "--dry-run",
    )
    assert (code, err) == (0, "")
    assert "\nBatch dry run\n" in out
    assert "No changes made (--dry-run)." in out
    assert held(music, ONE) == [] and held(music, TWO) == []


def test_each_export_is_written_to_its_own_playlist_in_its_own_order(music, songs, sync):
    empty_both(music)
    first, second, third = songs
    size = music.library_size()
    code, out, err = sync(
        [("Daily Mix TEST 1", [as_entry(third, 3), MISSING, as_entry(first, 1)]),
         ("Daily Mix TEST 2", [as_entry(second, 2), as_entry(first, 1)])],
        "--yes",
    )
    assert (code, err) == (0, "")
    assert held(music, ONE) == described([third, first])
    assert held(music, TWO) == described([second, first])
    assert "2 playlists updated successfully." in out
    assert "Intentionally left out:     1\n" in out
    # The song in both exports was matched for the first and remembered for the second.
    second_report = out[out.index("\nDaily Mix TEST 2\n") :]
    assert "Cached library matches:     1\n" in second_report
    assert music.library_size() == size, "nothing was added for the song that is not there"


def test_a_second_batch_replaces_the_contents_and_keeps_the_same_playlists(music, songs, sync):
    first, second, third = songs
    mixes = [("Daily Mix TEST 1", [as_entry(first, 1), as_entry(second, 2)]), ("Daily Mix TEST 2", [as_entry(third, 3)])]
    sync(mixes, "--yes")
    identities = [music.find_playlist(name).persistent_id for name in (ONE, TWO)]

    swapped = [("Daily Mix TEST 1", [as_entry(third, 3)]), ("Daily Mix TEST 2", [as_entry(second, 2), as_entry(first, 1)])]
    code, out, _ = sync(swapped, "--yes")
    assert code == 0
    assert held(music, ONE) == described([third])
    assert held(music, TWO) == described([second, first])
    assert [music.find_playlist(name).persistent_id for name in (ONE, TWO)] == identities
    assert out.count("✓ updated") == 2


def test_a_mix_with_nothing_in_the_library_leaves_its_playlist_as_it_was(music, songs, sync):
    first, second, _ = songs
    sync([("Daily Mix TEST 1", [as_entry(first, 1)]), ("Daily Mix TEST 2", [as_entry(second, 2)])], "--yes")
    code, out, err = sync(
        [("Daily Mix TEST 1", [MISSING]), ("Daily Mix TEST 2", [as_entry(first, 1), as_entry(second, 2)])],
        "--yes",
    )
    assert (code, err) == (0, "")
    assert held(music, ONE) == described([first]), "left as it was, not emptied"
    assert held(music, TWO) == described([first, second])
    assert "1 playlist updated successfully. 1 left unchanged" in out


def test_two_exports_for_one_playlist_are_refused_and_nothing_changes(music, songs, sync):
    before = held(music, ONE), held(music, TWO)
    first, second, _ = songs
    code, _, err = sync(
        [("Daily Mix TEST 1", [as_entry(first, 1)]), ("Daily Mix TEST 1", [as_entry(second, 2)])], "--yes"
    )
    assert code == 1
    assert "2 exports would be written to the same playlist, 'Spotify Daily Mix TEST 1'" in err
    assert (held(music, ONE), held(music, TWO)) == before


def test_without_confirmation_nothing_is_changed(music, songs, sync, monkeypatch):
    monkeypatch.setattr(cli, "_interactive", lambda: False)
    before = held(music, ONE), held(music, TWO)
    first, second, _ = songs
    code, _, err = sync(
        [("Daily Mix TEST 1", [as_entry(second, 2)]), ("Daily Mix TEST 2", [as_entry(first, 1)])]
    )
    assert code == 1 and "not confirmed" in err
    assert (held(music, ONE), held(music, TWO)) == before


def test_into_is_refused_for_a_batch_and_nothing_changes(music, songs, sync):
    before = held(music, ONE), held(music, TWO)
    first, second, _ = songs
    code, _, err = sync(
        [("Daily Mix TEST 1", [as_entry(second, 2)]), ("Daily Mix TEST 2", [as_entry(first, 1)])],
        "--yes", "--into", ONE,
    )
    assert code == 1 and "--into names a single playlist" in err
    assert (held(music, ONE), held(music, TWO)) == before
