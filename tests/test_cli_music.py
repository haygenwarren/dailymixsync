"""The music-* commands, run against the in-memory stand-in for Music."""

import pytest
from fake_music import FakeMusic, FakePlaylist

from daily_mix_sync import cli, music_app
from daily_mix_sync.models import AppleCandidate
from daily_mix_sync.music_app import MusicApp

TEST = "Spotify Daily Mix TEST"
LIBRARY = [
    AppleCandidate("AAA1", "Dancing Queen", "ABBA", "Arrival (Bonus Track Version)", 231_844),
    AppleCandidate("AAA2", "Dancing Queen", "Movie Cast", "Mamma Mia! (Soundtrack)", 221_093),
    AppleCandidate("NUT1", "Nutshell", "Alice In Chains", "Jar of Flies - EP", 259_000),
    AppleCandidate("NUT2", "Nutshell (Live)", "Alice In Chains", "MTV Unplugged (Live)", 297_000),
    AppleCandidate("LIV1", "Creep (Live)", "Radiohead", "Live Recordings", 260_000),
]


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """Point the CLI at an in-memory Music, and run from an empty directory."""
    fake = FakeMusic(
        library=LIBRARY,
        playlists=[
            FakePlaylist("LIKED00000000001", "Spotify Liked Songs", ["AAA1", "NUT1"]),
            FakePlaylist("SMART00000000001", "Recently Added", ["NUT2"], smart=True),
            FakePlaylist("FOLDER0000000001", "Mixes", kind="folder playlist"),
        ],
    )
    monkeypatch.setattr(cli, "MusicApp", lambda prefix: MusicApp(prefix, run=fake))
    monkeypatch.chdir(tmp_path)
    return fake


def run(capsys, *argv):
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_music_test_reports_what_music_answered(fake, capsys):
    code, out, _ = run(capsys, "music-test")
    assert code == 0
    assert out == (
        "Music:      version 1.6.3\n"
        "Library:    5 tracks\n"
        "Playlists:  3\n"
        "Managed:    none yet  (prefix 'Spotify Daily Mix')\n"
        "OK: Music answered every request.\n"
    )


def test_music_test_asks_nothing_about_the_music_window(fake, capsys):
    """Normal commands need Automation permission only; Accessibility is not consulted."""
    code, out, _ = run(capsys, "music-test")
    assert code == 0
    assert "Window control" not in out and "Accessibility" not in out
    assert not any("System Events" in script for script in fake.scripts_sent())


def test_music_test_starts_music_when_it_is_not_running(fake, capsys):
    fake.running = False
    code, out, _ = run(capsys, "music-test")
    assert code == 0
    assert "version 1.6.3  (was not running; started it)" in out
    assert fake.running


def test_music_test_explains_a_missing_automation_permission(fake, capsys):
    def denied(*args):
        raise music_app.error_from_osascript(
            "0:1: execution error: Not authorized to send Apple events to Music. (-1743)"
        )

    fake._handlers[music_app._PLAYLISTS] = denied
    code, out, err = run(capsys, "music-test")
    assert code == 1
    assert err.startswith("error: macOS has not allowed this program to control Music.")
    assert "Privacy & Security → Automation" in err
    assert "OK:" not in out


def test_music_playlists_marks_what_each_playlist_is(fake, capsys):
    fake.playlists.append(FakePlaylist("T1", TEST, ["AAA1"]))
    code, out, _ = run(capsys, "music-playlists")
    assert code == 0
    assert out == (
        "Tracks  Playlist\n"
        "     2  Spotify Liked Songs\n"
        "     1  Recently Added  [smart]\n"
        "     0  Mixes  [folder]\n"
        "     1  Spotify Daily Mix TEST  [managed]\n"
    )


def test_music_find_shows_candidates_and_picks_the_right_one(fake, capsys):
    code, out, _ = run(capsys, "music-find", "Dancing Queen", "ABBA")
    assert code == 0
    lines = out.splitlines()
    assert lines[0] == "Looking for: Dancing Queen — ABBA -:--"
    assert lines[1] == "Library search 'dancing queen abba': 1 candidate(s)"
    assert "100.0  Dancing Queen — ABBA [Arrival (Bonus Track Version)] 3:52  (id AAA1)" in out
    assert lines[-1] == "Result: match (score 100.0; accepted from 90)."


def test_music_find_prefers_the_studio_recording_over_the_live_one(fake, capsys):
    code, out, _ = run(capsys, "music-find", "Nutshell", "Alice In Chains")
    assert code == 0
    assert out.index("(id NUT1)") < out.index("(id NUT2)")
    assert "-30.0 version mismatch: live" in out


def test_music_find_uses_album_and_duration_when_given(fake, capsys):
    code, out, _ = run(
        capsys, "music-find", "Nutshell", "Alice In Chains",
        "--album", "Jar of Flies", "--duration", "4:19",
    )
    assert code == 0
    assert "Looking for: Nutshell — Alice In Chains [Jar of Flies] 4:19" in out
    assert "title 100, artist 100, album 100, duration 100" in out


def test_music_find_reports_a_track_that_is_not_in_the_library(fake, capsys):
    code, out, _ = run(capsys, "music-find", "Not There", "Nobody")
    assert code == 1
    assert out.endswith("Result: not in the Music library.\n")


def test_music_find_does_not_pass_off_a_wrong_version_as_a_match(fake, capsys):
    code, out, _ = run(capsys, "music-find", "Creep", "Radiohead")
    assert code == 1
    assert "Creep (Live)" in out
    assert out.endswith("Result: no confident match (best score 70.0; accepted from 90).\n")


@pytest.mark.parametrize("text", ["abc", "3:xx", "0", "-5", ""])
def test_bad_duration_is_a_usage_error(fake, capsys, text):
    with pytest.raises(SystemExit) as stop:
        cli.main(["music-find", "T", "A", f"--duration={text}"])
    assert stop.value.code == 2
    assert "is not a duration; use M:SS or seconds" in capsys.readouterr().err


@pytest.mark.parametrize(("text", "ms"), [("3:52", 232_000), ("232", 232_000), ("0:07.5", 7_500)])
def test_duration_formats(text, ms):
    assert cli._duration_arg(text) == ms


def test_music_add_test_creates_the_test_playlist_and_adds_the_track(fake, capsys):
    code, out, _ = run(capsys, "music-add-test", "Dancing Queen", "ABBA")
    assert code == 0
    assert out.endswith(
        "Added to 'Spotify Daily Mix TEST' (newly created playlist). "
        "Checked: it now holds 1 track(s).\n"
    )
    assert fake.playlist(TEST).track_ids == ["AAA1"]


def test_music_add_test_reuses_the_playlist_and_does_not_add_twice(fake, capsys):
    run(capsys, "music-add-test", "Dancing Queen", "ABBA")
    code, out, _ = run(capsys, "music-add-test", "Nutshell", "Alice In Chains")
    assert code == 0 and "(existing playlist). Checked: it now holds 2 track(s)." in out
    code, out, _ = run(capsys, "music-add-test", "Dancing Queen", "ABBA")
    assert code == 0 and out.endswith("already contains it; nothing was added.\n")
    assert fake.playlist(TEST).track_ids == ["AAA1", "NUT1"]
    assert [p.name for p in fake.playlists].count(TEST) == 1


@pytest.mark.parametrize("title_artist", [("Not There", "Nobody"), ("Creep", "Radiohead")])
def test_music_add_test_adds_nothing_without_a_confident_match(fake, capsys, title_artist):
    code, out, _ = run(capsys, "music-add-test", *title_artist)
    assert code == 1
    assert out.endswith("Nothing was added.\n")
    assert all(p.name != TEST for p in fake.playlists)  # not even created


def test_music_add_test_notices_when_the_track_did_not_arrive(fake, capsys):
    fake.ignore_adds = True
    code, _, err = run(capsys, "music-add-test", "Dancing Queen", "ABBA")
    assert code == 1
    assert "did not show up in 'Spotify Daily Mix TEST' (0 track(s) before, 0 after)" in err


def test_music_commands_only_ever_change_the_test_playlist(fake, capsys):
    run(capsys, "music-add-test", "Dancing Queen", "ABBA")
    run(capsys, "music-add-test", "Nutshell", "Alice In Chains")
    run(capsys, "music-clear-test")
    run(capsys, "music-add-test", "Nutshell", "Alice In Chains")
    run(capsys, "music-clear-test", "--delete")
    others = {p.name: p.track_ids for p in fake.playlists}
    assert others == {
        "Spotify Liked Songs": ["AAA1", "NUT1"], "Recently Added": ["NUT2"], "Mixes": [],
    }
    changing = {
        music_app._CREATE_PLAYLIST, music_app._ADD_TRACKS, music_app._REMOVE_TRACK,
        music_app._CLEAR_PLAYLIST, music_app._DELETE_PLAYLIST,
    }
    targets = {
        args[0] if script is music_app._CREATE_PLAYLIST else args[1]
        for script, args in fake.calls
        if script in changing
    }
    assert targets == {TEST}


def test_music_clear_test(fake, capsys):
    code, out, _ = run(capsys, "music-clear-test")
    assert (code, out) == (0, "There is no playlist named 'Spotify Daily Mix TEST'; nothing to do.\n")

    run(capsys, "music-add-test", "Dancing Queen", "ABBA")
    code, out, _ = run(capsys, "music-clear-test")
    assert (code, out) == (0, "Removed 1 track(s) from 'Spotify Daily Mix TEST'.\n")
    assert fake.playlist(TEST).track_ids == []

    code, out, _ = run(capsys, "music-clear-test", "--delete")
    assert (code, out) == (0, "Deleted 'Spotify Daily Mix TEST'.\n")
    assert all(p.name != TEST for p in fake.playlists)


def test_the_test_playlist_follows_the_configured_prefix(fake, capsys, tmp_path):
    (tmp_path / "config.json").write_text('{"managed_playlist_prefix": "Mirror"}')
    code, out, _ = run(capsys, "music-add-test", "Dancing Queen", "ABBA")
    assert code == 0 and "Added to 'Mirror TEST'" in out
    assert fake.playlist("Mirror TEST").track_ids == ["AAA1"]
    code, out, _ = run(capsys, "music-test")
    assert "Managed:    Mirror TEST  (prefix 'Mirror')" in out
