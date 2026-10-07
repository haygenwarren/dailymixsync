"""sync --catalog and the Music-window commands, against stand-ins for Music and its window."""

import builtins
import json
import sqlite3

import pytest
from fake_music import FakeMusic, FakePlaylist
from fake_ui import CatalogSong, FakeCatalogUI

from daily_mix_sync import catalog, cli
from daily_mix_sync.models import AppleCandidate
from daily_mix_sync.music_app import MusicApp
from daily_mix_sync.music_ui import MusicUIError, MusicUILayoutError

DREAMS = AppleCandidate("B1", "Dreams", "Fleetwood Mac", "Rumours", 257_800)
HOTEL = AppleCandidate("E1", "Hotel California", "Eagles", "Hotel California", 391_376)

CATALOG = [
    CatalogSong("901", "Mr. Brightside (Live)", "The Killers", "Live from the Royal Albert Hall", 250_000),
    CatalogSong("903", "Mr. Brightside", "The Killers", "Direct Hits", 223_973),
    CatalogSong("905", "Let It Go", "Idina Menzel", "Frozen (Original Motion Picture Soundtrack)", 223_840),
    CatalogSong("908", "X", "Zzz", "Whatever", 100_000),
]
TRACKS = [
    {"title": "Dreams", "artist": "Fleetwood Mac", "album": "Rumours", "duration_ms": 257000, "spotify_track_id": "s1"},
    {"title": "Mr. Brightside", "artist": "The Killers", "album": "Hot Fuss", "duration_ms": 222000, "spotify_track_id": "s2"},
    {"title": 'Let It Go - From "Frozen"', "artist": "Idina Menzel", "album": "Frozen (Original Motion Picture Soundtrack)", "duration_ms": 224000, "spotify_track_id": "s3"},
    {"title": "Not There", "artist": "Nobody", "spotify_track_id": "s4"},
    {"title": "Hotel California", "artist": "Eagles", "album": "Hotel California", "duration_ms": 391000, "spotify_track_id": "s5"},
]
DEST = "Spotify Daily Mix 1"
LIKED = "Spotify Liked Songs"
LIBRARY_ONLY = ["B1", "E1"]
WITH_CATALOG = ["B1", "LIB903", "E1"]  # Mr. Brightside lands where the export had it
WITH_CATALOG_REVIEW = ["B1", "LIB903", "LIB905", "E1"]


@pytest.fixture
def fake(tmp_path, monkeypatch):
    fake = FakeMusic(
        library=[DREAMS, HOTEL], playlists=[FakePlaylist("LIKED00000000001", LIKED, ["B1"])]
    )
    monkeypatch.setattr(cli, "MusicApp", lambda prefix: MusicApp(prefix, run=fake))
    monkeypatch.setattr(cli, "_interactive", lambda: False)
    monkeypatch.chdir(tmp_path)
    now = {"t": 0.0}
    monkeypatch.setattr(catalog.time, "sleep", lambda s: now.__setitem__("t", now["t"] + s))
    monkeypatch.setattr(catalog.time, "monotonic", lambda: now["t"])
    return fake


@pytest.fixture
def ui(fake, monkeypatch):
    ui = FakeCatalogUI(fake, CATALOG)
    monkeypatch.setattr(cli, "_catalog_ui", lambda: ui)
    return ui


@pytest.fixture
def export(tmp_path):
    def write(tracks=TRACKS, name="Daily Mix 1"):
        path = tmp_path / "daily_mix_1.json"
        path.write_text(json.dumps({"playlist_name": name, "tracks": tracks}), encoding="utf-8")
        return str(path)

    return write


@pytest.fixture
def keyboard(monkeypatch):
    def answers(*lines):
        remaining, prompts = list(lines), []

        def fake_input(prompt=""):
            prompts.append(prompt)
            if not remaining:
                raise EOFError
            return remaining.pop(0)

        monkeypatch.setattr(cli, "_interactive", lambda: True)
        monkeypatch.setattr(builtins, "input", fake_input)
        return prompts

    return answers


def run(capsys, *argv):
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def remembered(tmp_path):
    with sqlite3.connect(tmp_path / "data" / "mappings.sqlite3") as conn:
        return {k: (v, m) for k, v, m in conn.execute(
            "SELECT source_key, music_persistent_id, method FROM mappings")}


# --- sync: the flag -------------------------------------------------------------


@pytest.mark.parametrize("flags", [(), ("--no-catalog",)])
def test_without_the_flag_the_music_window_is_never_touched(fake, ui, export, capsys, flags):
    code, out, _ = run(capsys, "sync", export(), "--yes", *flags)
    assert code == 0
    assert ui.searches == [] and ui.sessions == 0 and ui.added == []
    assert fake.playlist(DEST).track_ids == LIBRARY_ONLY
    assert "Not in library:      3\n" in out
    assert "From catalog" not in out
    assert "`sync --catalog` looks for them there." in out


def test_catalog_flag_adds_missing_songs_and_writes_them_where_they_belong(
    fake, ui, export, capsys, tmp_path
):
    code, out, err = run(capsys, "sync", export(), "--yes", "--catalog")
    assert (code, err) == (0, "")
    assert fake.playlist(DEST).track_ids == WITH_CATALOG
    assert [r.catalog_id for r in ui.added] == ["903"]
    assert "Searching the Apple Music catalog for 3 track(s)." in out
    assert (
        "Tracks found:        5\n"
        "Cached matches:      0\n"
        "New matches:         2\n"
        "From catalog:        1\n"
        "Needs review:        0\n"
        "Not in library:      2\n"
    ) in out
    assert (
        "\nAdded to your library from the Apple Music catalog:\n"
        "  Mr. Brightside — The Killers  →  Mr. Brightside — The Killers\n"
    ) in out
    assert (
        "\nNot in the Music library:\n"
        '  Let It Go - From "Frozen" — Idina Menzel\n'
        "      catalog: the catalog's closest result needs review (Let It Go, score 80.0)\n"
        "  Not There — Nobody\n"
        "      catalog: no matching song in the catalog"
    ) in out
    assert "Verified:           3 / 3, in order" in out
    assert remembered(tmp_path)["spotify:track:s2"] == ("LIB903", "auto")
    assert ui.sessions == 1 and ui.handed_back == 1  # Music was given back afterwards


def test_catalog_songs_are_resolved_before_the_playlist_is_touched(fake, ui, export, capsys):
    fake.playlists.append(FakePlaylist("DEST000000000001", DEST, ["E1"]))
    run(capsys, "sync", export(), "--yes", "--catalog")
    assert "LIB903" in fake.library
    first_change = fake.changes()[0]
    assert first_change[1][1] == DEST  # the only thing changed through AppleScript
    assert fake.playlist(DEST).track_ids == WITH_CATALOG


def test_later_syncs_use_the_cache_and_leave_the_window_alone_for_those_songs(
    fake, ui, export, capsys
):
    run(capsys, "sync", export(), "--yes", "--catalog")
    ui.searches.clear()
    ui.added.clear()
    code, out, _ = run(capsys, "sync", export(), "--yes", "--catalog")
    assert code == 0
    assert "Cached matches:      3\n" in out and "From catalog:        0\n" in out
    assert ui.added == []
    assert ui.searches == ["let it go idina menzel", "not there nobody"]
    assert fake.playlist(DEST).track_ids == WITH_CATALOG

    ui.searches.clear()
    run(capsys, "sync", export(), "--yes")  # and without the flag, not at all
    assert ui.searches == []


def test_catalog_with_nothing_missing_does_not_open_a_session(fake, ui, export, capsys):
    code, _, _ = run(capsys, "sync", export(tracks=[TRACKS[0], TRACKS[4]]), "--yes", "--catalog")
    assert code == 0 and ui.sessions == 0 and ui.searches == []


# --- sync: dry run --------------------------------------------------------------


def test_dry_run_with_catalog_shows_what_it_would_add_and_adds_nothing(
    fake, ui, export, capsys, tmp_path
):
    before = dict(fake.library)
    code, out, _ = run(capsys, "sync", export(), "--dry-run", "--catalog")
    assert code == 0
    assert ui.added == [] and fake.library == before and fake.changes() == []
    assert "Would add:           1\n" in out and "Not in library:      2\n" in out
    assert (
        "\nWould add from the Apple Music catalog:\n"
        "  Mr. Brightside — The Killers  →  Mr. Brightside — The Killers\n"
    ) in out
    assert "New contents:     2 of 5 track(s), in playlist order" in out
    assert "plus 1 that would be added from the catalog" in out
    assert out.rstrip().endswith("No changes made (--dry-run).")
    assert "spotify:track:s2" not in remembered(tmp_path)  # nothing to remember yet
    assert all(p.name != DEST for p in fake.playlists)


def test_dry_run_with_catalog_asks_nothing(fake, ui, export, capsys, keyboard):
    prompts = keyboard("1", "y")
    run(capsys, "sync", export(), "--dry-run", "--catalog")
    assert prompts == [] and ui.added == []


# --- sync: permission and failures ----------------------------------------------


def test_missing_accessibility_permission_stops_before_anything_changes(fake, ui, export, capsys):
    ui.allowed = False
    fake.playlists.append(FakePlaylist("DEST000000000001", DEST, ["E1"]))
    code, out, err = run(capsys, "sync", export(), "--yes", "--catalog")
    assert code == 1
    assert err.startswith(
        "error: Searching the Apple Music catalog needs macOS Accessibility permission."
    )
    assert "System Settings → Privacy & Security → Accessibility" in err
    assert ui.searches == [] and ui.sessions == 0
    assert fake.changes() == [] and fake.playlist(DEST).track_ids == ["E1"]
    assert "✓" not in out


def test_one_song_failing_in_the_window_does_not_stop_the_sync(fake, ui, export, capsys):
    ui.fail_add = MusicUIError("The song result's More menu did not open.")
    code, out, _ = run(capsys, "sync", export(), "--yes", "--catalog")
    assert code == 0
    assert fake.playlist(DEST).track_ids == LIBRARY_ONLY
    assert "      catalog: The song result's More menu did not open." in out
    assert "✓ Playlist updated and verified." in out


def test_an_unexpected_window_layout_is_reported_and_the_rest_still_syncs(fake, ui, export, capsys):
    ui.fail_search["mr brightside killers"] = MusicUILayoutError(
        "The search field was not found in Music's toolbar. The Music window may have changed "
        "with an update. To see what is there now, run: python -m daily_mix_sync music-ui-inspect"
    )
    code, out, _ = run(capsys, "sync", export(), "--yes", "--catalog")
    assert code == 0
    assert "The catalog step ended early: The search field was not found in Music's toolbar." in out
    assert "python -m daily_mix_sync music-ui-inspect" in out
    assert ui.searches == ["mr brightside killers"]  # nothing more was tried
    assert fake.playlist(DEST).track_ids == LIBRARY_ONLY


def test_a_song_that_never_reaches_the_library_is_left_out_and_said_so(fake, ui, export, capsys, tmp_path):
    ui.appear_after = 10_000
    code, out, _ = run(capsys, "sync", export(), "--yes", "--catalog")
    assert code == 0
    assert fake.playlist(DEST).track_ids == LIBRARY_ONLY
    assert "was added to the library, but no track matching it well enough appeared" in out
    assert "the song stays in your library" in out
    assert "spotify:track:s2" not in remembered(tmp_path)


# --- sync: choosing a catalog result by hand ------------------------------------


def test_ambiguous_catalog_results_are_asked_about_and_the_choice_is_manual(
    fake, ui, export, capsys, keyboard, tmp_path
):
    prompts = keyboard("1", "y")
    code, out, _ = run(capsys, "sync", export(), "--catalog")
    assert code == 0
    assert prompts[0] == "Selection: " and prompts[1].startswith("\nCreate 'Spotify Daily Mix 1'")
    assert "Apple Music catalog match required" in out
    assert "Source:\n   Let It Go - From \"Frozen\"\n   Idina Menzel" in out
    assert "\n 1. Let It Go\n    Idina Menzel\n    Score: 80.0" in out
    assert " q. Stop catalog resolution" in out
    assert fake.playlist(DEST).track_ids == WITH_CATALOG_REVIEW
    assert remembered(tmp_path)["spotify:track:s3"] == ("LIB905", "manual")
    assert "From catalog:        2\n" in out and "Manual matches" not in out
    # The question is answered in the terminal, so Music is handed back first.
    assert ui.handed_back == 2


def test_skipping_the_catalog_question_leaves_the_song_out(fake, ui, export, capsys, keyboard):
    keyboard("s", "y")
    code, out, _ = run(capsys, "sync", export(), "--catalog")
    assert code == 0
    assert fake.playlist(DEST).track_ids == WITH_CATALOG
    assert "      catalog: catalog results were skipped in review" in out


def test_stopping_catalog_resolution_keeps_what_was_done_and_still_offers_the_write(
    fake, ui, export, capsys, keyboard
):
    keyboard("q", "y")
    code, out, _ = run(capsys, "sync", export(), "--catalog")
    assert code == 0
    assert "The catalog step ended early: stopped at your request" in out
    assert fake.playlist(DEST).track_ids == WITH_CATALOG
    assert "not there nobody" not in ui.searches


def test_no_review_also_silences_the_catalog_question(fake, ui, export, capsys, keyboard):
    prompts = keyboard("y")
    code, _, _ = run(capsys, "sync", export(), "--catalog", "--no-review")
    assert code == 0 and len(prompts) == 1
    assert fake.playlist(DEST).track_ids == WITH_CATALOG


# --- music-ui-inspect -----------------------------------------------------------


def test_inspect_lists_what_the_automation_relies_on(fake, ui, capsys):
    code, out, _ = run(capsys, "music-ui-inspect")
    assert code == 0
    assert "  ok       main window: Music, 1 window(s) in all\n" in out
    assert "  note     Songs section: not present" in out
    assert out.rstrip().endswith("Everything the catalog automation needs is in place.")
    assert ui.sessions == 1 and ui.added == []


def test_inspect_reports_missing_parts_and_fails(fake, ui, capsys):
    ui.layout = [("ok", "main window", "Music"), ("missing", "search field", "not found in the toolbar")]
    code, out, _ = run(capsys, "music-ui-inspect")
    assert code == 1
    assert "  MISSING  search field: not found in the toolbar\n" in out
    assert "1 expected part(s) not found." in out


def test_inspect_can_list_every_element(fake, ui, capsys):
    code, out, _ = run(capsys, "music-ui-inspect", "--dump")
    assert code == 0
    assert "Every element under the toolbar (2):\n  group 1 of toolbar\n  button Search of group 1 of toolbar\n" in out
    assert "Every element under the pane (2):" in out


def test_inspect_needs_accessibility(fake, ui, capsys):
    ui.allowed = False
    code, _, err = run(capsys, "music-ui-inspect")
    assert code == 1 and "Accessibility" in err and ui.sessions == 0


# --- music-catalog-search -------------------------------------------------------


def test_catalog_search_shows_scored_results_and_changes_nothing(fake, ui, capsys):
    before = dict(fake.library)
    code, out, _ = run(capsys, "music-catalog-search", "Mr. Brightside", "The Killers")
    assert code == 0
    assert out.startswith("Apple Music catalog search\n\nLooking for: Mr. Brightside — The Killers -:--\n")
    assert "Query:       mr brightside killers" in out
    assert "  100.0  Mr. Brightside — The Killers   (result 2 on the page)" in out
    assert "   70.0  Mr. Brightside (Live) — The Killers   (result 1 on the page)" in out
    assert "Result: Mr. Brightside — The Killers would be chosen (score 100.0; accepted from 90)." in out
    assert out.rstrip().endswith("Nothing was changed.")
    assert ui.added == [] and fake.library == before and fake.changes() == []


def test_catalog_search_exit_code_says_whether_a_confident_match_exists(fake, ui, capsys):
    code, out, _ = run(capsys, "music-catalog-search", 'Let It Go - From "Frozen"', "Idina Menzel")
    assert code == 1 and "Result: needs a choice by hand (best score 80.0" in out
    code, out, _ = run(capsys, "music-catalog-search", "Qwertyuiop", "Asdfghjkl")
    assert code == 1 and "Results: none" in out and "Result: nothing found." in out


# --- music-catalog-add-test -----------------------------------------------------


def test_add_test_adds_one_song_and_prints_its_persistent_id(fake, ui, capsys):
    code, out, _ = run(capsys, "music-catalog-add-test", "Mr. Brightside", "The Killers", "--yes")
    assert code == 0
    assert "Add to Library was chosen in Music." in out
    assert "The library now has: Mr. Brightside — The Killers [Direct Hits] 3:44" in out
    assert "Persistent ID:       LIB903" in out
    assert out.rstrip().endswith("It was not added to any playlist.")
    assert [r.catalog_id for r in ui.added] == ["903"]
    assert fake.changes() == []  # no playlist was created or changed
    assert [p.name for p in fake.playlists] == [LIKED]


def test_add_test_asks_first(fake, ui, capsys, keyboard):
    prompts = keyboard("n")
    code, out, _ = run(capsys, "music-catalog-add-test", "Mr. Brightside", "The Killers")
    assert code == 1 and ui.added == []
    assert prompts == ["\nAdd 'Mr. Brightside' by 'The Killers' to your Music library? [y/N] "]
    assert out.rstrip().endswith("Nothing was added.")

    keyboard("y")
    code, _, _ = run(capsys, "music-catalog-add-test", "Mr. Brightside", "The Killers")
    assert code == 0 and len(ui.added) == 1


def test_add_test_without_yes_and_nobody_to_ask_adds_nothing(fake, ui, capsys):
    code, _, err = run(capsys, "music-catalog-add-test", "Mr. Brightside", "The Killers")
    assert code == 1 and "error: not confirmed." in err and ui.added == []


def test_add_test_refuses_when_there_is_no_confident_match(fake, ui, capsys):
    code, out, _ = run(capsys, "music-catalog-add-test", "Not There", "Nobody", "--yes")
    assert code == 1 and out.rstrip().endswith("Nothing was added.") and ui.added == []
    # Ambiguous, and nobody to choose: also nothing.
    code, out, _ = run(capsys, "music-catalog-add-test", 'Let It Go - From "Frozen"', "Idina Menzel", "--yes")
    assert code == 1 and ui.added == []


def test_add_test_lets_a_person_choose_among_ambiguous_results(fake, ui, capsys, keyboard):
    keyboard("1", "y")
    code, out, _ = run(capsys, "music-catalog-add-test", 'Let It Go - From "Frozen"', "Idina Menzel")
    assert code == 0
    assert "Apple Music catalog match required" in out
    assert "Persistent ID:       LIB905" in out


def test_add_test_does_not_add_a_song_music_already_has(fake, ui, capsys):
    ui.in_library_ids.add("903")
    fake.library["LIB903"] = CATALOG[1].as_library_track()
    code, out, _ = run(capsys, "music-catalog-add-test", "Mr. Brightside", "The Killers", "--yes")
    assert code == 0 and ui.added == []
    assert "Music shows this song as already in your library; nothing was added." in out
    assert "Persistent ID:       LIB903" in out


def test_add_test_reports_a_song_that_does_not_show_up(fake, ui, capsys):
    ui.appear_after = 10_000
    code, _, err = run(capsys, "music-catalog-add-test", "Mr. Brightside", "The Killers", "--yes")
    assert code == 1
    assert "error: no matching track showed up in the library within 30 seconds." in err
