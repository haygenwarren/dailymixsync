"""The experimental catalog commands, against stand-ins for Music and its window.

These commands are not part of normal sync. They are the only way to reach the
catalog code, and the only commands that can add a song to the Music library.
"""

import builtins
import json
import sqlite3

import pytest
from fake_music import FakeMusic, FakePlaylist
from fake_ui import CatalogSong, FakeCatalogUI

from daily_mix_sync import catalog, cli, experimental
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
AFTER_FILL = ["B1", "LIB903", "E1"]  # Mr. Brightside lands where the export had it
FILL = "experimental-catalog-fill"


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
    monkeypatch.setattr(experimental, "_catalog_ui", lambda: ui)
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


# --- filling the library from an export -------------------------------------------


def test_fill_adds_the_missing_songs_to_the_library_and_touches_no_playlist(
    fake, ui, export, capsys, tmp_path
):
    code, out, err = run(capsys, FILL, export(), "--yes")
    assert (code, err) == (0, "")
    assert out.startswith("EXPERIMENTAL, not part of normal sync.")
    assert "can add songs to your Music library; the tool cannot remove them again." in out
    assert "Daily Mix 1: 3 of 5 track(s) are not in your library." in out
    assert "  - Mr. Brightside — The Killers\n" in out
    assert (
        "\nAdded to your library (1):\n"
        "  Mr. Brightside — The Killers  →  Mr. Brightside — The Killers\n"
    ) in out
    assert (
        "\nNot added (2):\n"
        '  Let It Go - From "Frozen" — Idina Menzel\n'
        "      the catalog's closest result needs review (Let It Go, score 80.0)\n"
        "  Not There — Nobody\n"
        "      no matching song in the catalog"
    ) in out
    assert "To put them in the playlist: sync " in out
    assert [r.catalog_id for r in ui.added] == ["903"] and "LIB903" in fake.library
    assert remembered(tmp_path)["spotify:track:s2"] == ("LIB903", "auto")
    assert fake.changes() == []  # no playlist was created or changed
    assert ui.sessions == 1 and ui.handed_back == 1  # the screen was given back


def test_a_normal_sync_afterwards_uses_what_was_added_without_the_window(fake, ui, export, capsys):
    run(capsys, FILL, export(), "--yes")
    ui.searches.clear()
    ui.sessions = 0
    code, out, _ = run(capsys, "sync", export(), "--yes")
    assert code == 0
    assert fake.playlist(DEST).track_ids == AFTER_FILL  # in export order
    assert ui.searches == [] and ui.sessions == 0 and len(ui.added) == 1
    assert "✓ Playlist updated and verified." in out


def test_fill_again_searches_only_for_what_is_still_missing(fake, ui, export, capsys):
    run(capsys, FILL, export(), "--yes")
    ui.searches.clear()
    ui.added.clear()
    code, out, _ = run(capsys, FILL, export(), "--yes")
    assert code == 0
    assert "Daily Mix 1: 2 of 5 track(s) are not in your library." in out
    assert ui.searches == ["let it go idina menzel", "not there nobody"] and ui.added == []
    assert out.rstrip().endswith("Nothing was added.")


def test_fill_with_nothing_missing_does_not_take_the_screen(fake, ui, export, capsys):
    code, out, _ = run(capsys, FILL, export(tracks=[TRACKS[0], TRACKS[4]]), "--yes")
    assert code == 0 and "Nothing to look for." in out
    assert ui.sessions == 0 and ui.searches == []


def test_fill_asks_before_adding_anything(fake, ui, export, capsys, keyboard):
    prompts = keyboard("n")
    code, out, _ = run(capsys, FILL, export(), "--no-review")
    assert code == 1 and ui.added == [] and ui.searches == []
    assert prompts == [
        "\nLook for these 3 in the Apple Music catalog and add the matches to your Music "
        "library? [y/N] "
    ]
    assert out.rstrip().endswith("Nothing was added.")

    keyboard("y")
    code, _, _ = run(capsys, FILL, export(), "--no-review")
    assert code == 0 and len(ui.added) == 1


def test_fill_without_yes_and_nobody_to_ask_adds_nothing(fake, ui, export, capsys):
    code, _, err = run(capsys, FILL, export())
    assert code == 1 and "error: not confirmed. Adding songs to your library needs a yes" in err
    assert ui.added == [] and ui.searches == []


def test_fill_dry_run_shows_what_it_would_add_and_adds_nothing(fake, ui, export, capsys, tmp_path, keyboard):
    prompts = keyboard("y", "1")
    before = dict(fake.library)
    code, out, _ = run(capsys, FILL, export(), "--dry-run")
    assert code == 0
    assert prompts == []  # a dry run asks nothing
    assert ui.added == [] and fake.library == before and fake.changes() == []
    assert (
        "\nWould add to your library (1):\n"
        "  Mr. Brightside — The Killers  →  Mr. Brightside — The Killers\n"
    ) in out
    assert out.rstrip().endswith("No changes made (--dry-run).")
    assert "spotify:track:s2" not in remembered(tmp_path)


def test_fill_needs_accessibility_and_stops_before_anything(fake, ui, export, capsys):
    ui.allowed = False
    code, out, err = run(capsys, FILL, export(), "--yes")
    assert code == 1
    assert err.startswith(
        "error: Searching the Apple Music catalog needs macOS Accessibility permission."
    )
    assert "System Settings → Privacy & Security → Accessibility" in err
    assert ui.searches == [] and ui.sessions == 0 and fake.changes() == []


def test_one_song_failing_in_the_window_does_not_stop_the_others(fake, ui, export, capsys):
    ui.fail_search["mr brightside killers"] = MusicUIError("Apple Music showed no results within 15 seconds.")
    code, out, _ = run(capsys, FILL, export(), "--yes")
    assert code == 0
    assert "  Mr. Brightside — The Killers\n      Apple Music showed no results within 15 seconds." in out
    assert "not there nobody" in ui.searches  # the rest were still tried


def test_an_unexpected_window_layout_ends_the_step_and_is_explained(fake, ui, export, capsys):
    ui.fail_search["mr brightside killers"] = MusicUILayoutError(
        "The search field was not found in Music's toolbar. The Music window may have changed "
        "with an update. To see what is there now, run: python -m daily_mix_sync experimental-ui-inspect"
    )
    code, out, _ = run(capsys, FILL, export(), "--yes")
    assert code == 0
    assert "The catalog step ended early: The search field was not found in Music's toolbar." in out
    assert ui.searches == ["mr brightside killers"]  # nothing more was tried


def test_a_song_that_never_reaches_the_library_is_reported(fake, ui, export, capsys, tmp_path):
    ui.appear_after = 10_000
    code, out, _ = run(capsys, FILL, export(), "--yes")
    assert code == 0
    assert "was added to the library, but no track matching it well enough appeared" in out
    assert "the song stays in your library" in out
    assert "spotify:track:s2" not in remembered(tmp_path)


def test_ambiguous_catalog_results_are_asked_about_and_the_choice_is_manual(
    fake, ui, export, capsys, keyboard, tmp_path
):
    prompts = keyboard("y", "1")
    code, out, _ = run(capsys, FILL, export())
    assert code == 0
    assert prompts[0].startswith("\nLook for these 3") and prompts[1] == "Selection: "
    assert "Apple Music catalog match required" in out
    assert "Source:\n   Let It Go - From \"Frozen\"\n   Idina Menzel" in out
    assert "\n 1. Let It Go\n    Idina Menzel\n    Score: 80.0" in out
    assert " q. Stop catalog resolution" in out
    assert remembered(tmp_path)["spotify:track:s3"] == ("LIB905", "manual")
    assert "\nAdded to your library (2):\n" in out
    # The question is answered in the terminal, so Music is handed back first.
    assert ui.handed_back == 2


def test_skipping_the_catalog_question_leaves_the_song_out(fake, ui, export, capsys, keyboard):
    keyboard("y", "s")
    code, out, _ = run(capsys, FILL, export())
    assert code == 0
    assert "      catalog results were skipped in review" in out
    assert [r.catalog_id for r in ui.added] == ["903"]


def test_stopping_catalog_resolution_keeps_what_was_done(fake, ui, export, capsys, keyboard):
    keyboard("y", "q")
    code, out, _ = run(capsys, FILL, export())
    assert code == 0
    assert "The catalog step ended early: stopped at your request" in out
    assert [r.catalog_id for r in ui.added] == ["903"]
    assert "not there nobody" not in ui.searches


def test_no_review_silences_the_catalog_question(fake, ui, export, capsys, keyboard):
    prompts = keyboard("y")
    code, _, _ = run(capsys, FILL, export(), "--no-review")
    assert code == 0 and len(prompts) == 1


# --- experimental-ui-inspect ------------------------------------------------------


def test_inspect_lists_what_the_automation_relies_on(fake, ui, capsys):
    code, out, _ = run(capsys, "experimental-ui-inspect")
    assert code == 0
    assert "  ok       main window: Music, 1 window(s) in all\n" in out
    assert "  note     Songs section: not present" in out
    assert out.rstrip().endswith("Everything the catalog automation needs is in place.")
    assert ui.sessions == 1 and ui.added == []


def test_inspect_reports_missing_parts_and_fails(fake, ui, capsys):
    ui.layout = [("ok", "main window", "Music"), ("missing", "search field", "not found in the toolbar")]
    code, out, _ = run(capsys, "experimental-ui-inspect")
    assert code == 1
    assert "  MISSING  search field: not found in the toolbar\n" in out
    assert "1 expected part(s) not found." in out


def test_inspect_can_list_every_element(fake, ui, capsys):
    code, out, _ = run(capsys, "experimental-ui-inspect", "--dump")
    assert code == 0
    assert "Every element under the toolbar (2):\n  group 1 of toolbar\n  button Search of group 1 of toolbar\n" in out
    assert "Every element under the pane (2):" in out


def test_inspect_needs_accessibility(fake, ui, capsys):
    ui.allowed = False
    code, _, err = run(capsys, "experimental-ui-inspect")
    assert code == 1 and "Accessibility" in err and ui.sessions == 0


# --- experimental-catalog-search --------------------------------------------------


def test_catalog_search_shows_scored_results_and_changes_nothing(fake, ui, capsys):
    before = dict(fake.library)
    code, out, _ = run(capsys, "experimental-catalog-search", "Mr. Brightside", "The Killers")
    assert code == 0
    assert out.startswith("Apple Music catalog search\n\nLooking for: Mr. Brightside — The Killers -:--\n")
    assert "Query:       mr brightside killers" in out
    assert "  100.0  Mr. Brightside — The Killers   (result 2 on the page)" in out
    assert "   70.0  Mr. Brightside (Live) — The Killers   (result 1 on the page)" in out
    assert "Result: Mr. Brightside — The Killers would be chosen (score 100.0; accepted from 90)." in out
    assert out.rstrip().endswith("Nothing was changed.")
    assert ui.added == [] and fake.library == before and fake.changes() == []


def test_catalog_search_exit_code_says_whether_a_confident_match_exists(fake, ui, capsys):
    code, out, _ = run(capsys, "experimental-catalog-search", 'Let It Go - From "Frozen"', "Idina Menzel")
    assert code == 1 and "Result: needs a choice by hand (best score 80.0" in out
    code, out, _ = run(capsys, "experimental-catalog-search", "Qwertyuiop", "Asdfghjkl")
    assert code == 1 and "Results: none" in out and "Result: nothing found." in out


# --- experimental-catalog-add-test ------------------------------------------------

ADD = "experimental-catalog-add-test"


def test_add_test_adds_one_song_and_prints_its_persistent_id(fake, ui, capsys):
    code, out, _ = run(capsys, ADD, "Mr. Brightside", "The Killers", "--yes")
    assert code == 0
    assert out.startswith("EXPERIMENTAL, not part of normal sync.")
    assert "Add to Library was chosen in Music." in out
    assert "The library now has: Mr. Brightside — The Killers [Direct Hits] 3:44" in out
    assert "Persistent ID:       LIB903" in out
    assert out.rstrip().endswith("It was not added to any playlist.")
    assert [r.catalog_id for r in ui.added] == ["903"]
    assert fake.changes() == []  # no playlist was created or changed
    assert [p.name for p in fake.playlists] == [LIKED]


def test_add_test_asks_first(fake, ui, capsys, keyboard):
    prompts = keyboard("n")
    code, out, _ = run(capsys, ADD, "Mr. Brightside", "The Killers")
    assert code == 1 and ui.added == []
    assert prompts == ["\nAdd 'Mr. Brightside' by 'The Killers' to your Music library? [y/N] "]
    assert out.rstrip().endswith("Nothing was added.")

    keyboard("y")
    code, _, _ = run(capsys, ADD, "Mr. Brightside", "The Killers")
    assert code == 0 and len(ui.added) == 1


def test_add_test_without_yes_and_nobody_to_ask_adds_nothing(fake, ui, capsys):
    code, _, err = run(capsys, ADD, "Mr. Brightside", "The Killers")
    assert code == 1 and "error: not confirmed." in err and ui.added == []


def test_add_test_refuses_when_there_is_no_confident_match(fake, ui, capsys):
    code, out, _ = run(capsys, ADD, "Not There", "Nobody", "--yes")
    assert code == 1 and out.rstrip().endswith("Nothing was added.") and ui.added == []
    # Ambiguous, and nobody to choose: also nothing.
    code, out, _ = run(capsys, ADD, 'Let It Go - From "Frozen"', "Idina Menzel", "--yes")
    assert code == 1 and ui.added == []


def test_add_test_lets_a_person_choose_among_ambiguous_results(fake, ui, capsys, keyboard):
    keyboard("1", "y")
    code, out, _ = run(capsys, ADD, 'Let It Go - From "Frozen"', "Idina Menzel")
    assert code == 0
    assert "Apple Music catalog match required" in out
    assert "Persistent ID:       LIB905" in out


def test_add_test_does_not_add_a_song_music_already_has(fake, ui, capsys):
    ui.in_library_ids.add("903")
    fake.library["LIB903"] = CATALOG[1].as_library_track()
    code, out, _ = run(capsys, ADD, "Mr. Brightside", "The Killers", "--yes")
    assert code == 0 and ui.added == []
    assert "Music shows this song as already in your library; nothing was added." in out
    assert "Persistent ID:       LIB903" in out


def test_add_test_reports_a_song_that_does_not_show_up(fake, ui, capsys):
    ui.appear_after = 10_000
    code, _, err = run(capsys, ADD, "Mr. Brightside", "The Killers", "--yes")
    assert code == 1
    assert "error: no matching track showed up in the library within 30 seconds." in err
