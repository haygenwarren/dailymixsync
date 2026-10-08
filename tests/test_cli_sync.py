"""match, review and sync against the in-memory stand-in for the Music library."""

import builtins
import json
import sqlite3

import pytest
from fake_music import FakeMusic, FakePlaylist

from daily_mix_sync import cli, music_app
from daily_mix_sync.models import AppleCandidate
from daily_mix_sync.music_app import MusicApp

BRIGHTSIDE = AppleCandidate("A1", "Mr. Brightside", "The Killers", "Hot Fuss", 222_973)
DREAMS = AppleCandidate("B1", "Dreams", "Fleetwood Mac", "Rumours", 257_800)
DREAMS_LIVE = AppleCandidate("B2", "Dreams (Live)", "Fleetwood Mac", "The Dance", 279_000)
LET_IT_GO = AppleCandidate("C1", "Let It Go", "Idina Menzel", "Frozen (Original Motion Picture Soundtrack)", 223_840)
CREEP_ACOUSTIC = AppleCandidate("D1", "Creep (Acoustic)", "Radiohead", "My Iron Lung - EP", 259_000)
HOTEL = AppleCandidate("E1", "Hotel California", "Eagles", "Hotel California", 391_376)
LIBRARY = [DREAMS_LIVE, BRIGHTSIDE, DREAMS, LET_IT_GO, CREEP_ACOUSTIC, HOTEL]

# A Daily Mix: three songs the library has, one that needs review, two it lacks.
TRACKS = [
    {"title": "Mr. Brightside", "artist": "The Killers", "album": "Hot Fuss", "duration_ms": 222000, "spotify_track_id": "s1"},
    {"title": 'Let It Go - From "Frozen"/Soundtrack Version', "artist": "Idina Menzel", "album": "Frozen (Original Motion Picture Soundtrack)", "duration_ms": 224000, "spotify_track_id": "s2"},
    {"title": "Creep", "artist": "Radiohead", "album": "Pablo Honey", "duration_ms": 238000, "spotify_track_id": "s3"},
    {"title": "Dreams", "artist": "Fleetwood Mac", "album": "Rumours", "duration_ms": 257000, "spotify_track_id": "s4"},
    {"title": "Not There", "artist": "Nobody", "spotify_track_id": "s5"},
    {"title": "Hotel California - 2013 Remaster", "artist": "Eagles", "album": "Hotel California (2013 Remaster)", "duration_ms": 391000, "spotify_track_id": "s6"},
]
DEST = "Spotify Daily Mix 1"
LIKED = "Spotify Liked Songs"
AUTOMATIC = ["A1", "B1", "E1"]  # what a sync writes when nobody reviews anything
WITH_REVIEW = ["A1", "C1", "B1", "E1"]  # ... and when "Let It Go" is accepted by hand


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """Point the CLI at an in-memory Music; run from an empty directory; nobody at the keyboard."""
    fake = FakeMusic(
        library=LIBRARY,
        playlists=[FakePlaylist("LIKED00000000001", LIKED, ["A1", "B1"])],
    )
    monkeypatch.setattr(cli, "MusicApp", lambda prefix: MusicApp(prefix, run=fake))
    monkeypatch.setattr(cli, "_interactive", lambda: False)
    monkeypatch.chdir(tmp_path)
    return fake


@pytest.fixture
def export(tmp_path):
    def write(name="Daily Mix 1", tracks=TRACKS, filename="daily_mix_1.json"):
        path = tmp_path / filename
        path.write_text(json.dumps({"playlist_name": name, "tracks": tracks}), encoding="utf-8")
        return str(path)

    return write


@pytest.fixture
def keyboard(monkeypatch):
    """Put someone at the keyboard who types the given answers, in order."""

    def answers(*lines):
        remaining = list(lines)
        prompts = []

        def fake_input(prompt=""):
            prompts.append(prompt)
            if not remaining:
                raise EOFError
            return remaining.pop(0)

        monkeypatch.setattr(cli, "_interactive", lambda: True)
        monkeypatch.setattr(builtins, "input", fake_input)
        return prompts

    return answers


def row(label, count):
    """One line of the summary of a run against the Music library."""
    return f"{label + ':':<25}{count:>4}\n"


def run(capsys, *argv):
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def stored(tmp_path, path="data/mappings.sqlite3"):
    with sqlite3.connect(tmp_path / path) as conn:
        return dict(conn.execute("SELECT source_key, music_persistent_id FROM mappings"))


# --- match ----------------------------------------------------------------------


def test_match_searches_the_music_library_when_no_mock_is_given(fake, export, capsys, tmp_path):
    code, out, _ = run(capsys, "match", export())
    assert code == 0
    assert out.startswith(
        "Daily Mix 1\n"
        "-----------\n"
        + row("Tracks in Spotify export", 6)
        + row("Cached library matches", 0)
        + row("New library matches", 3)
        + row("Needs review", 1)
        + row("Not in library", 2)
    )
    assert "Failed" not in out  # a song you do not have is not a failure
    assert "\nNot in your Music library, so left out:\n" in out
    assert "Only songs already in your library are used. Nothing is added to it." in out
    assert out.rstrip().endswith("Searched: your Music library   Mappings: data/mappings.sqlite3")
    assert stored(tmp_path) == {
        "spotify:track:s1": "A1", "spotify:track:s4": "B1", "spotify:track:s6": "E1",
    }
    assert fake.changes() == []  # matching reads Music, it never changes it


def test_match_still_uses_the_mock_catalog_and_its_own_database(
    fake, capsys, tmp_path, sample_playlist_path, mock_catalog_path
):
    code, out, _ = run(
        capsys, "match", str(sample_playlist_path), "--mock-catalog", str(mock_catalog_path)
    )
    assert code == 0
    assert "New matches:         8\nNeeds review:        1\nFailed:              2\n" in out
    assert "Mappings: data/mock_mappings.sqlite3" in out
    assert (tmp_path / "data" / "mock_mappings.sqlite3").exists()
    assert not (tmp_path / "data" / "mappings.sqlite3").exists()
    assert fake.calls == []  # Music was not consulted at all


def test_second_match_uses_the_cache_and_reports_a_stale_entry(fake, export, capsys):
    run(capsys, "match", export())
    del fake.library["B1"]  # Dreams leaves the library; only the live version remains
    code, out, err = run(capsys, "match", export())
    assert code == 0
    assert row("Cached library matches", 2) + row("New library matches", 0) in out
    assert row("Not in library", 3) in out
    assert row("Stale mappings", 1) in out
    assert "1 remembered track(s) had left the library and were matched again" in out
    assert "'Dreams' by 'Fleetwood Mac': the remembered auto match (id B1) is no longer" in err


# --- review ---------------------------------------------------------------------


def test_review_command_remembers_the_choice(fake, export, capsys, keyboard, tmp_path):
    keyboard("1")
    code, out, _ = run(capsys, "review", export())
    assert code == 0
    assert "Needs review (1 of 1)" in out and " 1. Let It Go" in out
    assert row("Manual matches", 1) + row("Not in library", 2) in out
    with sqlite3.connect(tmp_path / "data" / "mappings.sqlite3") as conn:
        remembered = conn.execute(
            "SELECT music_persistent_id, method FROM mappings WHERE source_key = 'spotify:track:s2'"
        ).fetchone()
    assert remembered == ("C1", "manual")

    code, out, _ = run(capsys, "review", export())
    assert out.startswith("Nothing needs review.\n")
    assert row("Cached library matches", 4) in out


def test_review_command_works_offline_with_the_mock_catalog(
    fake, capsys, keyboard, sample_playlist_path, mock_catalog_path
):
    keyboard("s")
    code, out, _ = run(
        capsys, "review", str(sample_playlist_path), "--mock-catalog", str(mock_catalog_path)
    )
    assert code == 0
    assert "Needs review (1 of 1)" in out
    assert "Needs review:        1\n" in out
    assert fake.calls == []


# --- sync: dry run --------------------------------------------------------------


def test_dry_run_reports_and_changes_nothing(fake, export, capsys, tmp_path):
    code, out, _ = run(capsys, "sync", export(), "--dry-run")
    assert code == 0
    assert row("New library matches", 3) + row("Needs review", 1) + row("Not in library", 2) in out
    assert (
        "\nDestination:\nSpotify Daily Mix 1\n\n"
        "Previous tracks:         (new playlist)\n" + row("New tracks", 3)
    ) in out
    assert out.rstrip().endswith("No changes made (--dry-run).")
    assert fake.changes() == []
    assert [p.name for p in fake.playlists] == [LIKED]  # not even created
    assert len(stored(tmp_path)) == 3  # matches are still remembered


def test_dry_run_against_an_existing_playlist_leaves_it_alone(fake, export, capsys):
    fake.playlists.append(FakePlaylist("DEST000000000001", DEST, ["D1", "B2"]))
    code, out, _ = run(capsys, "sync", export(), "--dry-run")
    assert code == 0
    assert "\nDestination:\nSpotify Daily Mix 1\n\n" + row("Previous tracks", 2) + row("New tracks", 3) in out
    assert fake.changes() == []
    assert fake.playlist(DEST).track_ids == ["D1", "B2"]


def test_dry_run_never_asks_anything(fake, export, capsys, keyboard):
    prompts = keyboard("1", "y")
    code, _, _ = run(capsys, "sync", export(), "--dry-run")
    assert code == 0 and prompts == []
    assert fake.changes() == []


# --- sync: writing --------------------------------------------------------------


def test_sync_writes_the_matched_tracks_in_source_order(fake, export, capsys):
    fake.playlists.append(FakePlaylist("DEST000000000001", DEST, ["D1", "B2"]))
    code, out, err = run(capsys, "sync", export(), "--yes")
    assert (code, err) == (0, "")
    assert fake.playlist(DEST).track_ids == AUTOMATIC
    assert fake.playlist(DEST).persistent_id == "DEST000000000001"  # same playlist as before
    assert "\nNeeds review:\n  - Let It Go" in out
    assert (
        "\nNot in your Music library, so left out:\n"
        "  - Creep — Radiohead  (closest: Creep (Acoustic), score 52.6)\n"
        "  - Not There — Nobody\n"
        "  Only songs already in your library are used. Nothing is added to it.\n"
    ) in out
    assert "\n1 track(s) need review and are left out. To decide them, run:\n" in out
    assert "  python -m daily_mix_sync review " in out
    assert out.endswith(
        "\nDestination:\nSpotify Daily Mix 1\n\n"
        + row("Previous tracks", 2)
        + row("New tracks", 3)
        + "\n✓ Playlist updated and verified.\n"
    )


def test_sync_creates_the_playlist_when_it_does_not_exist(fake, export, capsys):
    code, out, _ = run(capsys, "sync", export(), "--yes")
    assert code == 0
    assert "Previous tracks:         (new playlist)\n" + row("New tracks", 3) in out
    assert out.endswith("\n✓ Playlist updated and verified.\n")
    assert fake.playlist(DEST).track_ids == AUTOMATIC


def test_sync_resolves_everything_before_touching_the_playlist(fake, export, capsys):
    fake.playlists.append(FakePlaylist("DEST000000000001", DEST, ["D1", "B2"]))
    run(capsys, "sync", export(), "--yes")
    scripts = fake.scripts_sent()
    first_change = scripts.index(music_app._CLEAR_PLAYLIST)
    last_search = max(i for i, script in enumerate(scripts) if script is music_app._SEARCH)
    assert last_search < first_change


def test_sync_changes_only_its_destination(fake, export, capsys):
    run(capsys, "sync", export(), "--yes")
    run(capsys, "sync", export(name="Daily Mix 2", filename="two.json"), "--yes")
    assert fake.playlist(LIKED).track_ids == ["A1", "B1"]
    targets = {
        args[0] if script is music_app._CREATE_PLAYLIST else args[1]
        for script, args in fake.changes()
    }
    assert targets == {"Spotify Daily Mix 1", "Spotify Daily Mix 2"}


def test_second_sync_is_served_from_the_cache(fake, export, capsys):
    run(capsys, "sync", export(), "--yes")
    fake.calls.clear()
    code, out, _ = run(capsys, "sync", export(), "--yes")
    assert code == 0
    assert row("Cached library matches", 3) + row("New library matches", 0) in out
    assert row("Previous tracks", 3) + row("New tracks", 3) in out
    assert fake.playlist(DEST).track_ids == AUTOMATIC
    # Only the three unresolved tracks are searched for, each by title and artist and
    # then, as that settles nothing, by title alone.
    searched = [(args[0], args[2]) for script, args in fake.calls if script is music_app._SEARCH]
    assert searched == [
        ("let it go idina menzel", "all"), ("let it go", "names"),
        ("creep radiohead", "all"), ("creep", "names"),
        ("not there nobody", "all"), ("not there", "names"),
    ]


def test_sync_drops_a_stale_mapping_and_writes_what_is_still_there(fake, export, capsys):
    run(capsys, "sync", export(), "--yes")
    del fake.library["E1"]  # Hotel California leaves the library
    fake.playlist(DEST).track_ids.remove("E1")  # ... and so leaves the playlist
    code, out, err = run(capsys, "sync", export(), "--yes")
    assert code == 0
    assert row("Stale mappings", 1) in out
    assert row("Not in library", 3) in out
    assert "the remembered auto match (id E1) is no longer in the library" in err
    assert fake.playlist(DEST).track_ids == ["A1", "B1"]


def test_two_source_tracks_that_resolve_to_one_song_are_both_written(fake, export, capsys):
    tracks = [
        {"title": "Dreams", "artist": "Fleetwood Mac", "album": "Rumours", "spotify_track_id": "x1"},
        {"title": "Mr. Brightside", "artist": "The Killers", "spotify_track_id": "x2"},
        {"title": "Dreams - 2004 Remaster", "artist": "Fleetwood Mac", "album": "Rumours", "spotify_track_id": "x3"},
    ]
    code, _, _ = run(capsys, "sync", export(tracks=tracks), "--yes")
    assert code == 0
    assert fake.playlist(DEST).track_ids == ["B1", "A1", "B1"]


def test_exact_duplicate_entries_are_dropped_by_the_importer_and_reported(fake, export, capsys):
    tracks = [TRACKS[0], TRACKS[3], TRACKS[0]]
    code, out, _ = run(capsys, "sync", export(tracks=tracks), "--yes")
    assert code == 0
    assert row("Tracks in Spotify export", 2) in out and row("Duplicates", 1) in out
    assert fake.playlist(DEST).track_ids == ["A1", "B1"]


# --- sync: confirmation ---------------------------------------------------------


def test_sync_without_yes_and_nobody_to_ask_changes_nothing(fake, export, capsys):
    code, _, err = run(capsys, "sync", export())
    assert code == 1
    assert "error: not confirmed." in err and "--yes" in err
    assert fake.changes() == []


@pytest.mark.parametrize("answer", ["y", "Y", "yes", " Yes "])
def test_sync_asks_before_replacing_and_proceeds_on_yes(fake, export, capsys, keyboard, answer):
    fake.playlists.append(FakePlaylist("DEST000000000001", DEST, ["D1"]))
    prompts = keyboard(answer)
    code, out, _ = run(capsys, "sync", export(), "--no-review")
    assert code == 0
    assert prompts == ["\nReplace the contents of 'Spotify Daily Mix 1'? [y/N] "]
    assert row("Previous tracks", 1) + row("New tracks", 3) in out
    assert fake.playlist(DEST).track_ids == AUTOMATIC


@pytest.mark.parametrize("answer", ["n", "", "no", "maybe", "yep"])
def test_anything_but_yes_changes_nothing(fake, export, capsys, keyboard, answer):
    fake.playlists.append(FakePlaylist("DEST000000000001", DEST, ["D1"]))
    keyboard(answer)
    code, out, _ = run(capsys, "sync", export(), "--no-review")
    assert code == 1
    assert out.rstrip().endswith("Nothing was changed.")
    assert fake.changes() == []
    assert fake.playlist(DEST).track_ids == ["D1"]


def test_end_of_input_at_the_confirmation_counts_as_no(fake, export, capsys, keyboard):
    keyboard()
    code, _, _ = run(capsys, "sync", export(), "--no-review")
    assert code == 1 and fake.changes() == []


def test_interrupting_before_the_write_changes_nothing(fake, export, capsys, monkeypatch):
    fake.playlists.append(FakePlaylist("DEST000000000001", DEST, ["D1"]))

    def interrupt(prompt=""):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr(builtins, "input", interrupt)
    code, _, err = run(capsys, "sync", export(), "--no-review")
    assert code == 130 and err.endswith("interrupted\n")
    assert fake.changes() == []
    assert fake.playlist(DEST).track_ids == ["D1"]


def test_two_playlists_with_the_destination_name_stop_the_sync(fake, export, capsys):
    """Seen for real: iCloud brought a deleted playlist back next to its replacement."""
    fake.playlists.append(FakePlaylist("DEST000000000001", DEST, ["D1"]))
    fake.playlists.append(FakePlaylist("DEST000000000002", DEST, ["D1"]))
    code, _, err = run(capsys, "sync", export(), "--yes")
    assert code == 1
    assert "error: Music has 2 playlists named 'Spotify Daily Mix 1'" in err
    assert fake.changes() == []
    assert [p.track_ids for p in fake.playlists if p.name == DEST] == [["D1"], ["D1"]]


# --- sync: review ---------------------------------------------------------------


def test_sync_asks_about_ambiguous_tracks_and_writes_the_choice(fake, export, capsys, keyboard):
    prompts = keyboard("1", "y")
    code, out, _ = run(capsys, "sync", export())
    assert code == 0
    assert prompts == [
        "Selection: ",
        "\nCreate 'Spotify Daily Mix 1' with these 4 track(s)? [y/N] ",
    ]
    assert row("Manual matches", 1) + row("Not in library", 2) in out
    assert fake.playlist(DEST).track_ids == WITH_REVIEW  # in source order, not appended


def test_a_manual_choice_is_reused_by_later_unattended_syncs(fake, export, capsys, keyboard, monkeypatch):
    keyboard("1", "y")
    run(capsys, "sync", export())
    monkeypatch.setattr(cli, "_interactive", lambda: False)
    code, out, _ = run(capsys, "sync", export(), "--yes")
    assert code == 0
    assert row("Cached library matches", 4) in out and "Needs review" not in out
    assert fake.playlist(DEST).track_ids == WITH_REVIEW


def test_skipping_in_review_leaves_the_track_out(fake, export, capsys, keyboard):
    keyboard("s", "y")
    code, out, _ = run(capsys, "sync", export())
    assert code == 0
    assert row("Needs review", 1) in out
    assert fake.playlist(DEST).track_ids == AUTOMATIC


def test_quitting_review_still_leaves_the_decision_to_write(fake, export, capsys, keyboard):
    keyboard("q", "n")
    code, _, _ = run(capsys, "sync", export())
    assert code == 1 and fake.changes() == []


def test_no_review_flag_skips_the_questions(fake, export, capsys, keyboard):
    prompts = keyboard("y")
    code, _, _ = run(capsys, "sync", export(), "--no-review")
    assert code == 0 and len(prompts) == 1
    assert fake.playlist(DEST).track_ids == AUTOMATIC


def test_yes_with_someone_present_still_offers_review(fake, export, capsys, keyboard):
    prompts = keyboard("1")
    code, _, _ = run(capsys, "sync", export(), "--yes")
    assert code == 0 and prompts == ["Selection: "]
    assert fake.playlist(DEST).track_ids == WITH_REVIEW


# --- sync: nothing to write -----------------------------------------------------


def test_sync_with_nothing_resolvable_is_an_error_and_touches_nothing(fake, export, capsys):
    fake.playlists.append(FakePlaylist("DEST000000000001", DEST, ["D1", "B2"]))
    tracks = [TRACKS[2], TRACKS[4]]  # Creep (acoustic only) and a song nobody has
    code, out, err = run(capsys, "sync", export(tracks=tracks), "--yes")
    assert code == 1
    assert row("Not in library", 2) in out
    assert (
        "error: none of the 2 track(s) are in your Music library; "
        "'Spotify Daily Mix 1' was left alone"
    ) in err
    assert fake.changes() == []
    assert fake.playlist(DEST).track_ids == ["D1", "B2"]


def test_sync_with_an_empty_export_is_an_error(fake, export, capsys):
    code, _, err = run(capsys, "sync", export(tracks=[]), "--yes")
    assert code == 1 and "tracks list is empty" in err
    assert fake.calls == []


def test_sync_with_only_unusable_entries_is_an_error(fake, export, capsys):
    code, out, err = run(capsys, "sync", export(tracks=[{"title": "No Artist"}]), "--yes")
    assert code == 1
    assert row("Unusable entries", 1) in out
    assert "none of the 0 track(s) are in your Music library" in err
    assert fake.changes() == []


# --- sync: where it writes ------------------------------------------------------


@pytest.mark.parametrize(
    ("source_name", "destination"),
    [
        ("Daily Mix 2", "Spotify Daily Mix 2"),
        ("daily mix 6", "Spotify Daily Mix 6"),
        ("Spotify Liked Songs", "Spotify Daily Mix Spotify Liked Songs"),
        ("Road Trip", "Spotify Daily Mix Road Trip"),
    ],
)
def test_destination_comes_from_the_export_name_and_is_always_managed(
    fake, export, capsys, source_name, destination
):
    code, _, _ = run(capsys, "sync", export(name=source_name), "--yes")
    assert code == 0
    assert fake.playlist(destination).track_ids == AUTOMATIC
    assert fake.playlist(LIKED).track_ids == ["A1", "B1"]


def test_destination_follows_the_configured_prefix(fake, export, capsys, tmp_path):
    (tmp_path / "config.json").write_text('{"managed_playlist_prefix": "Mirror"}')
    code, _, _ = run(capsys, "sync", export(), "--yes")
    assert code == 0
    assert fake.playlist("Mirror Daily Mix 1").track_ids == AUTOMATIC


def test_into_writes_to_a_named_managed_playlist(fake, export, capsys):
    code, out, _ = run(capsys, "sync", export(), "--yes", "--into", "Spotify Daily Mix TEST")
    assert code == 0
    assert "\nDestination:\nSpotify Daily Mix TEST\n" in out
    assert fake.playlist("Spotify Daily Mix TEST").track_ids == AUTOMATIC
    assert all(p.name != DEST for p in fake.playlists)


@pytest.mark.parametrize("name", [LIKED, "Road Trip", "spotify daily mix 1", "Spotify Daily Mixtape"])
def test_into_an_unmanaged_playlist_is_refused_before_anything_happens(fake, export, capsys, name):
    code, out, err = run(capsys, "sync", export(), "--yes", "--into", name)
    assert code == 1
    assert f"error: refusing to write to playlist {name!r}" in err
    assert out == "" and fake.calls == []
    assert fake.playlist(LIKED).track_ids == ["A1", "B1"]


# --- sync: when writing goes wrong ----------------------------------------------


def test_verification_failure_is_reported_and_the_old_contents_restored(fake, export, capsys):
    fake.playlists.append(FakePlaylist("DEST000000000001", DEST, ["D1", "B2"]))
    real_add = fake._handlers[music_app._ADD_TRACKS]
    calls = []

    def drops_one_the_first_time(playlist_id, name, prefix, *track_ids):
        calls.append(track_ids)
        return real_add(playlist_id, name, prefix, *(track_ids[:-1] if len(calls) == 1 else track_ids))

    fake._handlers[music_app._ADD_TRACKS] = drops_one_the_first_time
    code, out, err = run(capsys, "sync", export(), "--yes")
    assert code == 1
    assert err == (
        "ERROR: updating 'Spotify Daily Mix 1' failed: Expected 3 track(s); Music reports 2.\n"
        "✓ Its previous contents were restored (2 track(s)).\n"
    )
    assert "✓ Playlist updated" not in out
    assert fake.playlist(DEST).track_ids == ["D1", "B2"]


def test_write_failure_rolls_back(fake, export, capsys):
    fake.playlists.append(FakePlaylist("DEST000000000001", DEST, ["D1", "B2"]))
    fake.fail_on[music_app._ADD_TRACKS] = [1]
    code, out, err = run(capsys, "sync", export(), "--yes")
    assert code == 1
    assert "ERROR: updating 'Spotify Daily Mix 1' failed:" in err and "Connection is invalid" in err
    assert "✓ Its previous contents were restored (2 track(s))." in err
    assert "✓ Playlist updated" not in out
    assert fake.playlist(DEST).track_ids == ["D1", "B2"]


def test_failed_rollback_says_manual_intervention_is_needed(fake, export, capsys):
    fake.playlists.append(FakePlaylist("DEST000000000001", DEST, ["D1", "B2"]))
    fake.fail_on[music_app._ADD_TRACKS] = [1, 2]
    code, out, err = run(capsys, "sync", export(), "--yes")
    assert code == 1
    assert "✗ Its previous contents could NOT be restored:" in err
    assert "Manual intervention is required: 'Spotify Daily Mix 1' may now be empty or incomplete." in err
    assert "    Creep (Acoustic) — Radiohead\n    Dreams (Live) — Fleetwood Mac\n" in err
    assert "✓" not in out
    assert fake.playlist(LIKED).track_ids == ["A1", "B1"]


def test_music_refusing_automation_is_explained(fake, export, capsys):
    def denied(*args):
        raise music_app.error_from_osascript(
            "0:1: execution error: Not authorized to send Apple events to Music. (-1743)"
        )

    fake._handlers[music_app._SEARCH] = denied
    code, _, err = run(capsys, "sync", export(), "--yes")
    assert code == 1
    assert err.startswith("error: macOS has not allowed this program to control Music.")
    assert fake.changes() == []
