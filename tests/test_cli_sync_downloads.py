"""sync-downloads: exports found in a Downloads folder, handed to the usual sync.

The folder is always a temporary one; the real ~/Downloads is never read. Music is
the in-memory stand-in.
"""

import builtins
import json
import os
import time
from datetime import UTC, datetime, timedelta

import pytest
from fake_music import FakeMusic, FakePlaylist

from daily_mix_sync import cli, music_app
from daily_mix_sync.models import AppleCandidate
from daily_mix_sync.music_app import MusicApp

NOW = datetime(2026, 10, 8, 13, 30, tzinfo=UTC)  # 9:30 AM in New York
SOURCE = "https://open.spotify.com/playlist/37i9dQZF1E35FIXTURE0001"
LIBRARY = [
    AppleCandidate("A1", "Mr. Brightside", "The Killers", "Hot Fuss", 222_973),
    AppleCandidate("B1", "Dreams", "Fleetwood Mac", "Rumours", 257_800),
    AppleCandidate("C1", "Let It Go", "Idina Menzel", "Frozen (Original Motion Picture Soundtrack)", 223_840),
    AppleCandidate("F1", "Paper Planes", "Ines Moreau", "Atlas", 198_000),
    AppleCandidate("G1", "Harbor Lights", "Juno Vale", "Harbor Lights", 215_000),
]


def track(title, artist, album="", duration_ms=None):
    entry = {"title": title, "artist": artist, "spotify_track_id": f"id-{title}"}
    if album:
        entry["album"] = album
    if duration_ms:
        entry["duration_ms"] = duration_ms
    return entry


BRIGHTSIDE = track("Mr. Brightside", "The Killers", "Hot Fuss", 222_000)
DREAMS = track("Dreams", "Fleetwood Mac", "Rumours", 257_000)
PAPER = track("Paper Planes", "Ines Moreau", "Atlas", 198_000)
HARBOR = track("Harbor Lights", "Juno Vale", "Harbor Lights", 215_000)
LET_IT_GO = track('Let It Go - From "Frozen"/Soundtrack Version', "Idina Menzel",
                  "Frozen (Original Motion Picture Soundtrack)", 224_000)
ABSENT = track("Not There", "Nobody")
LIKED = "Spotify Liked Songs"


@pytest.fixture(autouse=True)
def new_york_at_half_past_nine(monkeypatch):
    """Times are shown in local time; pin the zone and the clock so they can be checked."""
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    monkeypatch.setattr(cli, "_now", lambda: NOW)
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.fixture
def fake(tmp_path, monkeypatch):
    fake = FakeMusic(library=LIBRARY, playlists=[FakePlaylist("LIKED00000000001", LIKED, ["A1", "B1"])])
    monkeypatch.setattr(cli, "MusicApp", lambda prefix: MusicApp(prefix, run=fake))
    monkeypatch.setattr(cli, "_interactive", lambda: False)
    monkeypatch.chdir(tmp_path)
    return fake


@pytest.fixture
def folder(tmp_path):
    path = tmp_path / "Downloads"
    path.mkdir()
    return path


def export(folder, filename, name, tracks, minutes_ago=5.0, stamped=True):
    """Put an export in the folder, as the extension would have `minutes_ago`."""
    when = NOW - timedelta(minutes=minutes_ago)
    data = {"playlist_name": name, "source_url": SOURCE}
    if stamped:
        data["exported_at"] = when.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    data["tracks"] = tracks
    path = folder / filename
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.utime(path, (when.timestamp(), when.timestamp()))
    return path


@pytest.fixture
def keyboard(monkeypatch):
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


def run(capsys, folder, *options):
    code = cli.main(["sync-downloads", "--downloads-dir", str(folder), *options])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def contents(fake):
    return {playlist.name: list(playlist.track_ids) for playlist in fake.playlists}


def three_mixes(folder):
    export(folder, "daily_mix_1.json", "Daily Mix 1", [BRIGHTSIDE], minutes_ago=330)
    export(folder, "daily_mix_1 (1).json", "Daily Mix 1", [BRIGHTSIDE, DREAMS], minutes_ago=15)
    export(folder, "daily_mix_2.json", "Daily Mix 2", [PAPER, BRIGHTSIDE, ABSENT], minutes_ago=14)
    export(folder, "daily_mix_4.json", "Daily Mix 4", [HARBOR, PAPER], minutes_ago=12)


AFTER_THREE = {
    LIKED: ["A1", "B1"],
    "Spotify Daily Mix 1": ["A1", "B1"],
    "Spotify Daily Mix 2": ["F1", "A1"],
    "Spotify Daily Mix 4": ["G1", "F1"],
}


# --- nothing to sync ---------------------------------------------------------------------


def test_an_empty_folder_says_what_to_do_and_touches_nothing(fake, folder, capsys):
    code, out, err = run(capsys, folder, "--yes")
    assert code == 1
    assert out == (
        f"No recent Daily Mix exports found in {folder}.\n"
        "\n"
        "Export one or more Daily Mixes with the Chrome extension, then run this command again.\n"
    )
    assert err == ""
    assert fake.calls == []


def test_details_on_an_empty_folder_still_reads_cleanly(fake, folder, capsys):
    (folder / "package.json").write_text('{"name": "something"}')
    code, out, _ = run(capsys, folder, "--list", "--details")
    assert code == 1
    assert out == (
        f"Other JSON files in {folder}, passed over: 1\n"
        "\n"
        f"No recent Daily Mix exports found in {folder}.\n"
        "\n"
        "Export one or more Daily Mixes with the Chrome extension, then run this command again.\n"
    )


def test_a_folder_with_only_other_files_is_the_same_as_an_empty_one(fake, folder, capsys):
    (folder / "package.json").write_text('{"name": "something"}')
    (folder / "notes.json").write_text("not json")
    (folder / "photo.jpg").write_bytes(b"\xff\xd8")
    code, out, _ = run(capsys, folder, "--yes")
    assert code == 1 and out.startswith("No recent Daily Mix exports found")
    assert fake.calls == []


def test_a_folder_that_does_not_exist_is_explained(fake, tmp_path, capsys):
    code, out, err = run(capsys, tmp_path / "nowhere", "--yes")
    assert code == 1
    assert "error: cannot read" in err and "nowhere" in err
    assert "Privacy & Security → Files and Folders" in err and "--downloads-dir" in err
    assert out == ""
    assert fake.calls == []


# --- what is selected, and saying so ---------------------------------------------------------


def test_the_newest_export_of_each_mix_is_shown_before_anything_is_matched(fake, folder, capsys):
    three_mixes(folder)
    code, out, err = run(capsys, folder, "--yes")
    assert (code, err) == (0, "")
    assert out.startswith(
        f"Daily Mix exports found in {folder}:\n"
        "\n"
        "Daily Mix 1   daily_mix_1 (1).json   exported 9:15 AM\n"
        "Daily Mix 2   daily_mix_2.json       exported 9:16 AM\n"
        "Daily Mix 4   daily_mix_4.json       exported 9:18 AM\n"
        "\n"
        "Using 3 latest exports.\n"
        "Ignored 1 older export.\n"
        "\n"
        "Matching 3 exports against your Music library:\n"
    )
    assert contents(fake) == AFTER_THREE


def test_the_older_copy_is_not_synced_and_is_not_a_clash(fake, folder, capsys):
    three_mixes(folder)
    code, out, err = run(capsys, folder, "--yes")
    assert code == 0
    assert contents(fake)["Spotify Daily Mix 1"] == ["A1", "B1"], "the 9:15 export, not the earlier one"
    assert "would be written to the same playlist" not in err


def test_the_same_two_files_named_outright_are_still_refused(fake, folder, capsys):
    three_mixes(folder)
    code = cli.main(["sync", str(folder / "daily_mix_1.json"), str(folder / "daily_mix_1 (1).json"), "--yes"])
    _, err = capsys.readouterr()
    assert code == 1 and "2 exports would be written to the same playlist" in err
    assert fake.changes() == []


def test_one_export_goes_through_the_single_playlist_path(fake, folder, capsys):
    export(folder, "daily_mix_1.json", "Daily Mix 1", [BRIGHTSIDE, DREAMS])
    code, out, _ = run(capsys, folder, "--yes")
    assert code == 0
    assert out.startswith(
        f"Daily Mix exports found in {folder}:\n\n"
        "Daily Mix 1   daily_mix_1.json   exported 9:25 AM\n\n"
        "Using the latest export.\n\n"
        "Daily Mix 1\n-----------\n"
    )
    assert out.endswith("\n✓ Playlist updated and verified.\n")
    assert contents(fake)["Spotify Daily Mix 1"] == ["A1", "B1"]


def test_details_also_lists_what_was_passed_over(fake, folder, capsys):
    three_mixes(folder)
    (folder / "package.json").write_text('{"name": "something"}')
    (folder / "data.json").write_text("[1, 2, 3]")
    _, out, _ = run(capsys, folder, "--list", "--details")
    assert "\nOlder exports, not used:\n\n  Daily Mix 1   daily_mix_1.json   exported 4:00 AM\n" in out
    assert f"\nOther JSON files in {folder}, passed over: 2\n" in out


def test_without_details_older_copies_are_only_counted(fake, folder, capsys):
    three_mixes(folder)
    _, out, _ = run(capsys, folder, "--list")
    assert "Ignored 1 older export.\n" in out
    assert "Older exports, not used" not in out and "passed over" not in out


def test_a_file_time_is_called_saved_and_an_export_time_exported(fake, folder, capsys):
    export(folder, "a.json", "Daily Mix 1", [BRIGHTSIDE], minutes_ago=20)
    export(folder, "b.json", "Daily Mix 2", [PAPER], minutes_ago=5, stamped=False)
    _, out, _ = run(capsys, folder, "--list")
    assert "Daily Mix 1   a.json   exported 9:10 AM\n" in out
    assert "Daily Mix 2   b.json   saved 9:25 AM\n" in out


def test_times_from_other_days_say_which_day(fake, folder, capsys):
    export(folder, "a.json", "Daily Mix 1", [BRIGHTSIDE], minutes_ago=12 * 60)  # 9:30 PM the day before
    export(folder, "b.json", "Daily Mix 2", [PAPER], minutes_ago=60 * 24 * 3 + 90)  # three days ago, 8:00 AM
    export(folder, "c.json", "Daily Mix 3", [DREAMS], minutes_ago=9 * 60 + 45)  # 11:45 PM the day before
    _, out, _ = run(capsys, folder, "--list", "--max-age", "200")
    assert "Daily Mix 1   a.json   exported yesterday, 9:30 PM\n" in out
    assert "Daily Mix 2   b.json   exported Oct 5, 8:00 AM\n" in out
    assert "Daily Mix 3   c.json   exported yesterday, 11:45 PM\n" in out


def test_a_folder_under_home_is_shown_with_a_tilde(fake, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "Downloads").mkdir()
    export(tmp_path / "Downloads", "daily_mix_1.json", "Daily Mix 1", [BRIGHTSIDE])
    code = cli.main(["sync-downloads", "--list"])  # no folder named: the default
    out, _ = capsys.readouterr()
    assert code == 0
    assert out.startswith("Daily Mix exports found in ~/Downloads:\n")
    assert str(tmp_path) not in out


# --- where it looks ---------------------------------------------------------------------------


def test_the_default_folder_is_downloads_in_the_home_folder(fake, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    downloads = tmp_path / "home" / "Downloads"
    downloads.mkdir(parents=True)
    export(downloads, "daily_mix_1.json", "Daily Mix 1", [BRIGHTSIDE, DREAMS])
    code = cli.main(["sync-downloads", "--yes"])
    capsys.readouterr()
    assert code == 0
    assert contents(fake)["Spotify Daily Mix 1"] == ["A1", "B1"]


def test_the_folder_can_be_set_in_the_config_and_the_option_overrides_it(fake, tmp_path, capsys):
    configured, other = tmp_path / "from_config", tmp_path / "from_option"
    configured.mkdir()
    other.mkdir()
    export(configured, "a.json", "Daily Mix 1", [BRIGHTSIDE])
    export(other, "b.json", "Daily Mix 2", [PAPER])
    (tmp_path / "config.json").write_text(json.dumps({"downloads_dir": str(configured)}), encoding="utf-8")
    cli.main(["sync-downloads", "--list"])
    assert "a.json" in capsys.readouterr().out
    cli.main(["sync-downloads", "--list", "--downloads-dir", str(other)])
    out = capsys.readouterr().out
    assert "b.json" in out and "a.json" not in out


# --- exports that are too old -------------------------------------------------------------------


def test_an_old_export_is_left_out_and_named_and_the_rest_are_synced(fake, folder, capsys):
    three_mixes(folder)
    export(folder, "daily_mix_3.json", "Daily Mix 3", [DREAMS], minutes_ago=60 * 24 * 3 + 90)
    code, out, err = run(capsys, folder, "--yes")
    assert (code, err) == (0, "")
    assert (
        "\nNot used, because older than 24 hours:\n"
        "\n"
        "Daily Mix 3   daily_mix_3.json   exported Oct 5, 8:00 AM\n"
        "\n"
        "Export it again, or allow older exports with --max-age HOURS.\n"
    ) in out
    assert "Spotify Daily Mix 3" not in contents(fake)
    assert contents(fake) == AFTER_THREE


def test_max_age_lets_older_exports_in(fake, folder, capsys):
    export(folder, "daily_mix_3.json", "Daily Mix 3", [DREAMS], minutes_ago=60 * 30)
    code, out, _ = run(capsys, folder, "--yes")
    assert code == 1 and "Not used, because older than 24 hours" in out
    assert fake.calls == []
    code, out, _ = run(capsys, folder, "--yes", "--max-age", "48")
    assert code == 0 and "Not used" not in out
    assert contents(fake)["Spotify Daily Mix 3"] == ["B1"]


def test_max_age_can_also_be_made_stricter(fake, folder, capsys):
    export(folder, "daily_mix_1.json", "Daily Mix 1", [BRIGHTSIDE], minutes_ago=90)
    code, out, _ = run(capsys, folder, "--yes", "--max-age", "1")
    assert code == 1
    assert "Not used, because older than 1 hour:" in out
    code, out, _ = run(capsys, folder, "--list", "--max-age", "0.5")
    assert "Not used, because older than 0.5 hours:" in out


def test_the_age_limit_can_be_set_in_the_config_and_the_option_overrides_it(fake, folder, tmp_path, capsys):
    export(folder, "daily_mix_3.json", "Daily Mix 3", [DREAMS], minutes_ago=60 * 30)
    (tmp_path / "config.json").write_text('{"export_max_age_hours": 72}', encoding="utf-8")
    assert run(capsys, folder, "--list")[0] == 0
    code, out, _ = run(capsys, folder, "--list", "--max-age", "12")
    assert code == 1 and "older than 12 hours" in out


def test_only_old_exports_means_nothing_is_done(fake, folder, capsys):
    export(folder, "daily_mix_1.json", "Daily Mix 1", [BRIGHTSIDE], minutes_ago=60 * 24 * 20)
    export(folder, "daily_mix_2.json", "Daily Mix 2", [PAPER], minutes_ago=60 * 24 * 21)
    code, out, _ = run(capsys, folder, "--yes")
    assert code == 1
    assert "Not used, because older than 24 hours:" in out
    assert "Export them again, or allow older exports with --max-age HOURS." in out
    assert out.rstrip().endswith("then run this command again.")
    assert "No recent Daily Mix exports found" in out
    assert fake.calls == []


@pytest.mark.parametrize("value", ["0", "-3", "soon", "inf", "nan"])
def test_a_max_age_that_is_not_a_positive_number_is_refused(fake, folder, capsys, value):
    with pytest.raises(SystemExit) as stopped:
        cli.main(["sync-downloads", "--downloads-dir", str(folder), "--max-age", value])
    assert stopped.value.code == 2
    assert "expected a number of hours greater than 0" in capsys.readouterr().err
    assert fake.calls == []


# --- damaged exports ------------------------------------------------------------------------------


def damaged(folder, filename, name="Daily Mix 2", minutes_ago=2.0):
    path = folder / filename
    path.write_text(f'{{\n  "playlist_name": "{name}",\n  "source_url": "{SOURCE}",\n  "tracks": [\n    {{"title": "Pap', encoding="utf-8")
    when = (NOW - timedelta(minutes=minutes_ago)).timestamp()
    os.utime(path, (when, when))
    return path


def test_a_damaged_newest_export_keeps_its_mix_out_and_the_others_go_ahead(fake, folder, capsys):
    three_mixes(folder)
    damaged(folder, "daily_mix_2 (1).json")
    code, out, err = run(capsys, folder, "--yes")
    assert code == 1, "the run did not do everything that was asked of it"
    assert "Daily Mix 2   daily_mix_2.json" not in out.split("Using")[0]
    assert "Using 2 latest exports." in out
    assert (
        "\nCannot be used:\n"
        "\n"
        "daily_mix_2 (1).json: it is not valid JSON; the file is damaged or incomplete\n"
        "  It is the newest export of Daily Mix 2. The one before it, daily_mix_2.json (exported 9:16 AM),\n"
        "  is not used in its place, since it may be out of date.\n"
        "  Export Daily Mix 2 again, or delete the damaged file.\n"
    ) in out
    assert "Spotify Daily Mix 2" not in contents(fake), "the older export was not quietly used"
    assert contents(fake)["Spotify Daily Mix 1"] == ["A1", "B1"]
    assert contents(fake)["Spotify Daily Mix 4"] == ["G1", "F1"]
    assert f"error: 1 export in {folder} could not be used; see the top of this output." in err


def test_a_damaged_export_with_nothing_older_is_reported_too(fake, folder, capsys):
    export(folder, "daily_mix_1.json", "Daily Mix 1", [BRIGHTSIDE])
    damaged(folder, "daily_mix_7.json", "Daily Mix 7")
    code, out, err = run(capsys, folder, "--yes")
    assert code == 1
    assert "daily_mix_7.json: it is not valid JSON" in out
    assert "  Export that Daily Mix again, or delete the damaged file.\n" in out
    assert contents(fake)["Spotify Daily Mix 1"] == ["A1"]
    assert "could not be used" in err


def test_only_a_damaged_export_means_nothing_is_done(fake, folder, capsys):
    damaged(folder, "daily_mix_2.json")
    code, out, _ = run(capsys, folder, "--yes")
    assert code == 1
    assert "Cannot be used:" in out and "No recent Daily Mix exports found" in out
    assert fake.calls == []


def test_a_damaged_export_that_a_newer_one_replaced_is_not_a_problem(fake, folder, capsys):
    damaged(folder, "daily_mix_2.json", minutes_ago=200)
    export(folder, "daily_mix_2 (1).json", "Daily Mix 2", [PAPER], minutes_ago=3)
    code, out, err = run(capsys, folder, "--yes", "--details")
    assert (code, err) == (0, "")
    assert "Cannot be used" not in out
    assert "Damaged exports that no longer matter, as newer ones exist or they are old: daily_mix_2.json" in out
    assert contents(fake)["Spotify Daily Mix 2"] == ["F1"]


def test_an_export_that_does_not_name_its_playlist_never_becomes_a_playlist(fake, folder, capsys):
    export(folder, "daily_mix_1 (1).json", "", [BRIGHTSIDE])
    code, out, _ = run(capsys, folder, "--yes")
    assert code == 1
    assert "daily_mix_1 (1).json: it does not say which playlist it is" in out
    assert fake.calls == []


# --- listing only -----------------------------------------------------------------------------------


def test_list_shows_the_selection_and_stops(fake, folder, capsys):
    three_mixes(folder)
    code, out, err = run(capsys, folder, "--list")
    assert (code, err) == (0, "")
    assert out == (
        f"Daily Mix exports found in {folder}:\n"
        "\n"
        "Daily Mix 1   daily_mix_1 (1).json   exported 9:15 AM\n"
        "Daily Mix 2   daily_mix_2.json       exported 9:16 AM\n"
        "Daily Mix 4   daily_mix_4.json       exported 9:18 AM\n"
        "\n"
        "Using 3 latest exports.\n"
        "Ignored 1 older export.\n"
    )
    assert fake.calls == [], "Music was not asked anything"


def test_list_does_not_need_music_or_the_mapping_database(fake, folder, capsys, tmp_path, monkeypatch):
    three_mixes(folder)
    monkeypatch.setattr(cli, "MusicApp", lambda prefix: pytest.fail("Music was reached"))
    assert run(capsys, folder, "--list")[0] == 0
    assert not (tmp_path / "data").exists()


def test_list_reports_a_problem_with_its_exit_status(fake, folder, capsys):
    three_mixes(folder)
    damaged(folder, "daily_mix_2 (1).json")
    code, out, _ = run(capsys, folder, "--list")
    assert code == 1 and "Cannot be used:" in out
    assert fake.calls == []


# --- the usual sync options ---------------------------------------------------------------------------


def test_a_dry_run_matches_and_changes_nothing(fake, folder, capsys):
    three_mixes(folder)
    code, out, err = run(capsys, folder, "--dry-run")
    assert (code, err) == (0, "")
    assert "\nBatch dry run\n" in out and "\nWould update:\n" in out
    assert out.endswith("\nNo changes made (--dry-run).\n")
    assert fake.changes() == []
    assert contents(fake) == {LIKED: ["A1", "B1"]}


def test_the_whole_batch_is_confirmed_once(fake, folder, capsys, keyboard):
    three_mixes(folder)
    prompts = keyboard("y")
    code, _, _ = run(capsys, folder)
    assert code == 0
    assert prompts == ["\nContinue? [y/N] "]
    assert contents(fake) == AFTER_THREE


def test_saying_no_changes_nothing(fake, folder, capsys, keyboard):
    three_mixes(folder)
    keyboard("n")
    code, out, _ = run(capsys, folder)
    assert code == 1 and "Nothing was changed." in out
    assert fake.changes() == []


def test_yes_asks_nothing(fake, folder, capsys, keyboard):
    three_mixes(folder)
    prompts = keyboard()
    assert run(capsys, folder, "--yes", "--no-review")[0] == 0
    assert prompts == []


def test_with_nobody_to_ask_and_no_yes_nothing_is_changed(fake, folder, capsys):
    three_mixes(folder)
    code, _, err = run(capsys, folder)
    assert code == 1 and "not confirmed" in err
    assert fake.changes() == []


def test_review_is_offered_and_no_review_turns_it_off(fake, folder, capsys, keyboard):
    export(folder, "daily_mix_1.json", "Daily Mix 1", [BRIGHTSIDE, LET_IT_GO])
    export(folder, "daily_mix_2.json", "Daily Mix 2", [PAPER])
    prompts = keyboard("1", "y")
    code, out, _ = run(capsys, folder)
    assert code == 0
    assert prompts == ["Selection: ", "\nContinue? [y/N] "]
    assert "Daily Mix 1 — needs review" in out
    assert contents(fake)["Spotify Daily Mix 1"] == ["A1", "C1"]

    prompts = keyboard("y")
    code, _, _ = run(capsys, folder, "--no-review", "--db", "other.sqlite3")
    assert code == 0
    assert prompts == ["\nContinue? [y/N] "]
    assert contents(fake)["Spotify Daily Mix 1"] == ["A1"]


def test_the_mapping_database_can_be_named(fake, folder, capsys, tmp_path):
    three_mixes(folder)
    assert run(capsys, folder, "--yes", "--db", "elsewhere.sqlite3")[0] == 0
    assert (tmp_path / "elsewhere.sqlite3").exists() and not (tmp_path / "data").exists()


def test_into_is_refused_whatever_was_found(fake, folder, capsys):
    export(folder, "daily_mix_1.json", "Daily Mix 1", [BRIGHTSIDE])  # one export only
    code, out, err = run(capsys, folder, "--yes", "--into", "Spotify Daily Mix TEST")
    assert code == 1
    assert "--into is not available with sync-downloads" in err
    assert "sync FILE --into NAME" in err
    assert out == ""
    assert fake.calls == []


def test_into_is_not_advertised(capsys):
    with pytest.raises(SystemExit):
        cli.main(["sync-downloads", "--help"])
    assert "--into" not in capsys.readouterr().out


# --- one implementation of syncing -----------------------------------------------------------------------


def test_the_selected_files_are_handed_to_the_same_code_as_sync(fake, folder, capsys, monkeypatch):
    three_mixes(folder)
    handed = []
    monkeypatch.setattr(cli, "_sync_paths", lambda args, paths: handed.append((args, list(paths))) or 0)
    assert run(capsys, folder, "--yes", "--no-review", "--details")[0] == 0
    cli.main(["sync", "one.json", "two.json", "--dry-run"])
    (found_args, found_paths), (named_args, named_paths) = handed
    assert found_paths == [folder / "daily_mix_1 (1).json", folder / "daily_mix_2.json", folder / "daily_mix_4.json"]
    assert [path.name for path in named_paths] == ["one.json", "two.json"]
    # The same options reach it either way.
    for option in ("dry_run", "yes", "no_review", "db", "details", "destination", "config"):
        assert hasattr(found_args, option) and hasattr(named_args, option), option
    assert (found_args.yes, found_args.no_review, found_args.details, found_args.dry_run) == (True, True, True, False)
    assert found_args.destination is None


def test_the_result_is_the_same_as_naming_the_selected_files(fake, folder, capsys):
    three_mixes(folder)
    code = cli.main(["sync", str(folder / "daily_mix_1 (1).json"), str(folder / "daily_mix_2.json"),
                     str(folder / "daily_mix_4.json"), "--dry-run", "--db", "a.sqlite3"])
    named = capsys.readouterr().out
    assert code == 0
    code, found, _ = run(capsys, folder, "--dry-run", "--db", "b.sqlite3")
    assert code == 0
    assert found.endswith(named), "after the list of what was found, the output is the same"


def test_a_failed_write_is_handled_as_in_any_batch(fake, folder, capsys):
    three_mixes(folder)
    fake.fail_on = {music_app._ADD_TRACKS: [2]}
    code, out, err = run(capsys, folder, "--yes")
    assert code == 1
    assert "ERROR: updating 'Spotify Daily Mix 2' failed" in err
    assert "✗ write failed; previous contents restored" in out
    assert contents(fake)["Spotify Daily Mix 1"] == ["A1", "B1"]
    assert contents(fake)["Spotify Daily Mix 4"] == ["G1", "F1"]


# --- safety ---------------------------------------------------------------------------------------------


def test_the_files_stay_exactly_where_and_as_they_were(fake, folder, capsys):
    three_mixes(folder)
    damaged(folder, "daily_mix_9.json", "Daily Mix 9")
    (folder / "package.json").write_text('{"name": "something"}')

    def snapshot():
        return {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in sorted(folder.iterdir())}

    before = snapshot()
    run(capsys, folder, "--yes")
    assert snapshot() == before


def test_only_managed_playlists_are_ever_written(fake, folder, capsys):
    export(folder, "liked.json", "Spotify Liked Songs", [PAPER])
    export(folder, "mix.json", "Daily Mix 1", [BRIGHTSIDE])
    code, _, _ = run(capsys, folder, "--yes")
    assert code == 0
    assert contents(fake)[LIKED] == ["A1", "B1"], "the user's own playlist of that name is not the target"
    for script, args in fake.changes():
        name = args[0] if script is music_app._CREATE_PLAYLIST else args[1]
        assert name.startswith("Spotify Daily Mix ")


def test_nothing_is_added_to_the_library_and_no_window_automation_is_used(fake, folder, capsys):
    three_mixes(folder)
    before = dict(fake.library)
    assert run(capsys, folder, "--yes")[0] == 0
    assert fake.library == before
    assert set(fake.scripts_sent()) <= set(fake._handlers)
    for script in fake.scripts_sent():
        assert "System Events" not in script and "Add to Library" not in script


def test_a_mix_with_nothing_in_the_library_is_left_alone_as_in_any_batch(fake, folder, capsys):
    export(folder, "daily_mix_1.json", "Daily Mix 1", [BRIGHTSIDE])
    export(folder, "daily_mix_5.json", "Daily Mix 5", [ABSENT])
    code, out, _ = run(capsys, folder, "--yes")
    assert code == 0
    assert "Spotify Daily Mix 5" not in contents(fake)
    assert "1 playlist updated successfully. 1 left unchanged" in out
