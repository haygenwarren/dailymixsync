"""Unit tests for the Music adapter. Nothing here talks to the real Music app."""

import re
import subprocess

import pytest
from fake_music import FakeMusic, FakePlaylist

from daily_mix_sync import music_app
from daily_mix_sync.models import AppleCandidate
from daily_mix_sync.music_app import (
    AccessibilityPermissionError,
    MusicApp,
    MusicAppError,
    MusicPermissionError,
    MusicPlaylist,
    UnmanagedPlaylistError,
    error_from_osascript,
    run_osascript,
)

DANCING = AppleCandidate("AAA1", "Dancing Queen", "ABBA", "Arrival", 231_844)
SHOOK = AppleCandidate("BBB2", "You Shook Me All Night Long", "AC/DC", "Back In Black", 210_329)
DEJA = AppleCandidate("CCC3", "Déjà Vu (feat. JAY Z)", "Beyoncé", "", None)
LIBRARY = [DANCING, SHOOK, DEJA]

TEST = "Spotify Daily Mix TEST"
LIKED = "Spotify Liked Songs"

READ_SCRIPTS = {
    "is running": music_app._IS_RUNNING,
    "version": music_app._VERSION,
    "library size": music_app._LIBRARY_SIZE,
    "playlists": music_app._PLAYLISTS,
    "playlist tracks": music_app._PLAYLIST_TRACKS,
    "search": music_app._SEARCH,
    "has track": music_app._HAS_TRACK,
}
CHANGE_SCRIPTS = {
    "add tracks": music_app._ADD_TRACKS,
    "remove track": music_app._REMOVE_TRACK,
    "clear playlist": music_app._CLEAR_PLAYLIST,
    "delete playlist": music_app._DELETE_PLAYLIST,
}


@pytest.fixture
def fake():
    """A library of three songs, the user's own playlist, and an empty test playlist."""
    return FakeMusic(
        library=LIBRARY,
        playlists=[
            FakePlaylist("LIKED00000000001", LIKED, ["AAA1", "BBB2"]),
            FakePlaylist("TEST000000000001", TEST),
        ],
    )


@pytest.fixture
def music(fake):
    return MusicApp(run=fake)


def code_of(script: str) -> str:
    """The script without its comments."""
    return "\n".join(line for line in script.splitlines() if not line.strip().startswith("--"))


# --- osascript ---------------------------------------------------------------


def completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def test_run_osascript_passes_values_as_arguments_after_a_separator(monkeypatch):
    seen = {}

    def fake_run(command, **kwargs):
        seen["command"], seen["kwargs"] = command, kwargs
        return completed("  two words \n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    reply = run_osascript("the script", ["-1", 'it\'s "quoted"'], timeout=5)
    assert seen["command"] == ["osascript", "-e", "the script", "--", "-1", 'it\'s "quoted"']
    assert seen["kwargs"]["timeout"] == 5
    assert reply == "  two words "  # only osascript's own trailing newline is dropped


def test_run_osascript_raises_on_failure(monkeypatch):
    stderr = "0:1: execution error: Not authorized to send Apple events to Music. (-1743)\n"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: completed(stderr=stderr, returncode=1))
    with pytest.raises(MusicPermissionError, match="Privacy & Security → Automation"):
        run_osascript("x")


def test_run_osascript_timeout_mentions_a_possible_permission_prompt(monkeypatch):
    def hang(*args, **kwargs):
        raise subprocess.TimeoutExpired("osascript", 3)

    monkeypatch.setattr(subprocess, "run", hang)
    with pytest.raises(MusicAppError, match=r"within 3s.*permission prompt"):
        run_osascript("x", timeout=3)


def test_run_osascript_off_macos(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("osascript")

    monkeypatch.setattr(subprocess, "run", missing)
    with pytest.raises(MusicAppError, match="needs macOS"):
        run_osascript("x")


@pytest.mark.parametrize(
    ("stderr", "kind", "message"),
    [
        # Captured from the real app unless marked otherwise.
        (
            "382:416: execution error: Music got an error: Parameter error. (-50)",
            MusicAppError,
            r"Parameter error\. \(AppleScript error -50\)",
        ),
        (
            "8:60: execution error: Music got an error: Can’t get persistent ID of every "
            "track of user playlist id 29104 of source id 61. (-1728)",
            MusicAppError,
            r"Can’t get persistent ID .* \(AppleScript error -1728\)",
        ),
        (
            "6:40: execution error: Spotify Liked Songs is not a managed playlist (9001)",
            UnmanagedPlaylistError,
            "refused by the safety check in Music: Spotify Liked Songs is not a managed",
        ),
        (
            "6:40: execution error: the playlist no longer exists (9002)",
            MusicAppError,
            "the playlist no longer exists",
        ),
        (
            "69:104: execution error: System Events got an error: osascript is not allowed "
            "assistive access. (-25211)",
            AccessibilityPermissionError,
            "Privacy & Security → Accessibility",
        ),
        (
            "68:72: execution error: System Events got an error: osascript is not allowed "
            "assistive access. (-1719)",
            AccessibilityPermissionError,
            "operate other apps' windows",
        ),
        # Documented AppleScript errors, not reproduced live.
        (
            "0:1: execution error: Not authorized to send Apple events to Music. (-1743)",
            MusicPermissionError,
            "has not allowed this program to control Music",
        ),
        (
            "0:1: execution error: Music got an error: AppleEvent timed out. (-1712)",
            MusicAppError,
            "did not answer in time",
        ),
        (
            "0:1: execution error: Music got an error: Application isn’t running. (-600)",
            MusicAppError,
            "not running or could not be started",
        ),
        ("osascript: something unexpected", MusicAppError, "osascript failed: osascript: some"),
        ("", MusicAppError, "no error text"),
    ],
)
def test_osascript_errors_are_translated(stderr, kind, message):
    error = error_from_osascript(stderr)
    assert type(error) is kind
    assert re.search(message, str(error))


# --- the scripts themselves ----------------------------------------------------


@pytest.mark.parametrize("name", CHANGE_SCRIPTS)
def test_every_changing_script_starts_with_the_safety_check(name):
    body = code_of(CHANGE_SCRIPTS[name]).split("on run argv")[-1]
    first_statement = body.strip().splitlines()[0].strip()
    assert first_statement == (
        "set p to my managedPlaylist(item 1 of argv, item 2 of argv, item 3 of argv)"
    )


@pytest.mark.parametrize("name", READ_SCRIPTS)
def test_reading_scripts_contain_no_changing_command(name):
    body = code_of(READ_SCRIPTS[name]).split("on run argv")[-1]
    assert not re.search(r"\b(delete|duplicate|make|move|add)\b", body)
    assert "set name of" not in body


def test_tracks_are_only_deleted_through_a_playlist_never_the_library():
    for script in CHANGE_SCRIPTS.values():
        for line in code_of(script).splitlines():
            if re.search(r"\bdelete\b", line):
                assert "library playlist" not in line
                assert re.search(r"delete (\(every track of p\b|every track of p\b|p$)", line)


def test_scripts_avoid_names_that_music_reserves():
    # Inside `tell application "Music"`, these are search-area constants, and
    # assigning to them fails with error -10006.
    for script in {**READ_SCRIPTS, **CHANGE_SCRIPTS}.values():
        assert not re.search(r"\b(names|artists|albums|composers|displayed)\b", code_of(script))


def test_reading_tracks_guards_against_the_empty_playlist_error():
    # `persistent ID of every track of p` is error -1728 when p is empty.
    script = code_of(music_app._PLAYLIST_TRACKS)
    guard = script.index("if (count of tracks of p) is 0 then return")
    assert guard < script.index("persistent ID of every track of p")


# --- the application -----------------------------------------------------------


def test_running_version_and_library_size(music):
    assert music.is_running() is True
    assert music.version() == "1.6.3"
    assert music.library_size() == 3


def test_launch_does_nothing_when_music_is_running(music, fake):
    music.launch()
    assert music_app._LAUNCH not in fake.scripts_sent()


def test_launch_starts_music_and_waits_for_it(fake):
    fake.running = False
    MusicApp(run=fake).launch()
    assert music_app._LAUNCH in fake.scripts_sent()
    assert fake.running


def test_launch_gives_up_after_the_timeout(monkeypatch):
    replies = {music_app._IS_RUNNING: "false", music_app._LAUNCH: ""}
    monkeypatch.setattr(music_app.time, "sleep", lambda seconds: None)
    with pytest.raises(MusicAppError, match="did not start within 0s"):
        MusicApp(run=lambda script, args: replies[script]).launch(timeout=0)


@pytest.mark.parametrize("prefix", ["", "  ", " Spotify Daily Mix", "Spotify Daily Mix "])
def test_blank_or_padded_prefix_is_refused(prefix):
    with pytest.raises(ValueError):
        MusicApp(prefix, run=lambda script, args: "")


# --- reading -------------------------------------------------------------------


def test_playlists(music):
    assert music.playlists() == [
        MusicPlaylist("LIKED00000000001", LIKED, "user playlist", "none", False, 2),
        MusicPlaylist("TEST000000000001", TEST, "user playlist", "none", False, 0),
    ]


def test_find_playlist_requires_the_exact_name(music):
    assert music.find_playlist(TEST).persistent_id == "TEST000000000001"
    # Music itself would hand back the playlist for any capitalisation.
    assert music.find_playlist("spotify daily mix test") is None
    assert music.find_playlist("Spotify Daily Mix") is None
    assert music.find_playlist("No Such Playlist") is None


@pytest.mark.parametrize(
    "lookalike",
    [
        FakePlaylist("X1", TEST, kind="folder playlist"),
        FakePlaylist("X2", TEST, smart=True),
        FakePlaylist("X3", TEST, special_kind="Music"),
    ],
)
def test_find_playlist_ignores_folders_smart_and_built_in_playlists(lookalike):
    assert MusicApp(run=FakeMusic(playlists=[lookalike])).find_playlist(TEST) is None


def test_two_playlists_with_the_same_name_are_not_guessed_between():
    fake = FakeMusic(playlists=[FakePlaylist("A", TEST), FakePlaylist("B", TEST)])
    with pytest.raises(MusicAppError, match="has 2 playlists named 'Spotify Daily Mix TEST'"):
        MusicApp(run=fake).find_playlist(TEST)
    with pytest.raises(MusicAppError, match="has 2 playlists"):
        MusicApp(run=fake).clear_playlist(TEST)


def test_playlist_tracks(music):
    assert music.playlist_tracks(LIKED) == [DANCING, SHOOK]
    assert music.playlist_tracks(TEST) == []
    with pytest.raises(MusicAppError, match="no playlist named 'Nope'"):
        music.playlist_tracks("Nope")


def test_search_finds_songs_and_parses_them(music):
    assert music.search_songs("dancing queen abba", 10) == [DANCING]
    assert music.search_songs("deja vu beyonce", 10) == [DEJA]  # no album, no duration
    assert music.search_songs("nothing like this", 10) == []


def test_search_sends_the_term_and_limit_as_arguments(music, fake):
    music.search_songs("you shook me all night long ac dc", 7)
    script, args = fake.calls[-1]
    assert script is music_app._SEARCH
    assert args == ["you shook me all night long ac dc", "7"]
    assert "shook" not in script  # values never end up inside the script text


def test_search_respects_the_limit(music):
    assert len(music.search_songs("a", 1)) == 1


def test_search_drops_the_word_and():
    # "Rock & Roll" normalizes to "rock and roll", but the library entry may say "&",
    # and one absent word makes Music return nothing.
    sent = []
    MusicApp(run=lambda script, args: sent.append(args) or "").search_songs(
        "rock And roll led zeppelin", 5
    )
    assert sent == [["rock roll led zeppelin", "5"]]


@pytest.mark.parametrize("term", ["", "   ", "and", " and  AND "])
def test_search_with_nothing_to_search_for_sends_no_script(music, fake, term):
    assert music.search_songs(term, 10) == []
    assert fake.calls == []


def test_has_track(music):
    assert music.has_track("AAA1") is True
    assert music.has_track("ZZZ9") is False


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("ID1\x1fTitle\x1fArtist\x1fAlbum\x1f200000", [AppleCandidate("ID1", "Title", "Artist", "Album", 200000)]),
        ("ID1\x1fTitle\x1fArtist\x1f\x1f", [AppleCandidate("ID1", "Title", "Artist", "", None)]),
        ("ID1\x1fTitle\x1fArtist\x1fAlbum\x1f5.4E+8", [AppleCandidate("ID1", "Title", "Artist", "Album", 540000000)]),
        ("ID1\x1fA, B & C\x1fX\ty\x1f \x1f1", [AppleCandidate("ID1", "A, B & C", "X\ty", " ", 1)]),
        ("", []),
    ],
)
def test_track_replies_are_parsed(reply, expected):
    assert MusicApp(run=lambda script, args: reply).search_songs("x", 5) == expected


@pytest.mark.parametrize("reply", ["only\x1fthree\x1ffields", "a\x1fb\x1fc\x1fd\x1fe\x1ff", "junk"])
def test_malformed_reply_is_an_error_not_a_guess(reply):
    with pytest.raises(MusicAppError, match="unexpected reply from Music"):
        MusicApp(run=lambda script, args: reply).search_songs("x", 5)


# --- the safety rule -----------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "managed"),
    [
        ("Spotify Daily Mix", True),
        ("Spotify Daily Mix 1", True),
        ("Spotify Daily Mix TEST", True),
        ("Spotify Daily Mixtape", False),
        ("Spotify Daily Mix2", False),
        ("Spotify Liked Songs", False),
        ("spotify daily mix 1", False),
        ("My Spotify Daily Mix 1", False),
        (" Spotify Daily Mix 1", False),
        ("Spotify", False),
        ("", False),
    ],
)
def test_which_names_are_managed(music, name, managed):
    assert music.is_managed(name) is managed


CHANGES = {
    "ensure_playlist": lambda m, name: m.ensure_playlist(name),
    "add_tracks": lambda m, name: m.add_tracks(name, ["AAA1"]),
    "remove_track": lambda m, name: m.remove_track(name, "AAA1"),
    "clear_playlist": lambda m, name: m.clear_playlist(name),
    "delete_playlist": lambda m, name: m.delete_playlist(name),
}


@pytest.mark.parametrize("change", CHANGES)
@pytest.mark.parametrize(
    "name", [LIKED, "Spotify Daily Mixtape", "spotify daily mix test", "Road Trip", ""]
)
def test_unmanaged_playlists_are_refused_before_anything_is_sent(music, fake, change, name):
    with pytest.raises(UnmanagedPlaylistError, match="refusing to change playlist"):
        CHANGES[change](music, name)
    assert fake.calls == []
    assert fake.playlist(LIKED).track_ids == ["AAA1", "BBB2"]


def test_the_script_refuses_too_if_the_playlist_was_renamed_meanwhile(fake):
    """Second line of defence: Python thinks the name is managed, Music disagrees."""

    def renaming_runner(script, args):
        reply = fake(script, args)
        if script is music_app._PLAYLISTS:
            fake.playlist(TEST).name = "Road Trip"  # the user renames it in Music
        return reply

    music = MusicApp(run=renaming_runner)
    with pytest.raises(UnmanagedPlaylistError, match="expected Spotify Daily Mix TEST but found"):
        music.clear_playlist(TEST)


def test_a_different_prefix_moves_the_boundary(fake):
    music = MusicApp("Mirror", run=fake)
    assert music.is_managed("Mirror 1") and not music.is_managed(TEST)
    with pytest.raises(UnmanagedPlaylistError):
        music.clear_playlist(TEST)


# --- changing managed playlists ------------------------------------------------


def test_ensure_playlist_reuses_an_existing_one(music, fake):
    assert music.ensure_playlist(TEST).persistent_id == "TEST000000000001"
    assert music_app._CREATE_PLAYLIST not in fake.scripts_sent()


def test_ensure_playlist_creates_a_missing_one(music, fake):
    created = music.ensure_playlist("Spotify Daily Mix 1")
    assert created.name == "Spotify Daily Mix 1" and created.track_count == 0
    assert (music_app._CREATE_PLAYLIST, ["Spotify Daily Mix 1"]) in fake.calls
    assert music.ensure_playlist("Spotify Daily Mix 1") == created
    assert fake.scripts_sent().count(music_app._CREATE_PLAYLIST) == 1


def test_ensure_playlist_reports_a_creation_that_did_not_take():
    silent = FakeMusic()
    silent._handlers[music_app._CREATE_PLAYLIST] = lambda name: "PL1"  # creates nothing
    with pytest.raises(MusicAppError, match="reported creating .* but it cannot be found"):
        MusicApp(run=silent).ensure_playlist(TEST)


def test_add_tracks_appends_in_order_and_names_the_target_three_ways(music, fake):
    assert music.add_tracks(TEST, ["BBB2", "AAA1", "CCC3"]) == []
    assert fake.playlist(TEST).track_ids == ["BBB2", "AAA1", "CCC3"]
    assert fake.calls[-1] == (
        music_app._ADD_TRACKS,
        ["TEST000000000001", TEST, "Spotify Daily Mix", "BBB2", "AAA1", "CCC3"],
    )
    assert music.playlist_tracks(TEST) == [SHOOK, DANCING, DEJA]


def test_add_tracks_reports_ids_missing_from_the_library(music, fake):
    assert music.add_tracks(TEST, ["GONE1", "AAA1", "GONE2"]) == ["GONE1", "GONE2"]
    assert fake.playlist(TEST).track_ids == ["AAA1"]


def test_add_tracks_with_nothing_to_add_sends_nothing(music, fake):
    assert music.add_tracks(TEST, []) == []
    assert fake.calls == []


def test_changing_a_missing_managed_playlist_is_an_error(music):
    for change in ("add_tracks", "remove_track", "clear_playlist"):
        with pytest.raises(MusicAppError, match="no playlist named 'Spotify Daily Mix 9'"):
            CHANGES[change](music, "Spotify Daily Mix 9")


def test_remove_track_takes_out_every_copy_and_leaves_the_library(music, fake):
    music.add_tracks(TEST, ["AAA1", "BBB2", "AAA1"])
    assert music.remove_track(TEST, "AAA1") == 2
    assert fake.playlist(TEST).track_ids == ["BBB2"]
    assert music.remove_track(TEST, "AAA1") == 0
    assert music.has_track("AAA1")
    assert fake.playlist(LIKED).track_ids == ["AAA1", "BBB2"]


def test_clear_playlist(music, fake):
    music.add_tracks(TEST, ["AAA1", "BBB2"])
    assert music.clear_playlist(TEST) == 2
    assert music.clear_playlist(TEST) == 0
    assert music.find_playlist(TEST).persistent_id == "TEST000000000001"
    assert music.library_size() == 3


def test_delete_playlist(music, fake):
    assert music.delete_playlist(TEST) is True
    assert music.find_playlist(TEST) is None
    assert [p.name for p in fake.playlists] == [LIKED]
    assert music.delete_playlist(TEST) is False
    assert fake.scripts_sent().count(music_app._DELETE_PLAYLIST) == 1
