"""Several exports in one sync, against the in-memory stand-in for the Music library.

What a batch has to get right, beyond what a single sync does:

- everything is loaded, checked and matched before any playlist is touched;
- two exports never end up in the same playlist;
- there is one question for the whole batch;
- each playlist is written on its own, and one that fails is put back without
  stopping or undoing the others;
- nothing about the library-only rule changes.
"""

import builtins
import json
import sqlite3

import pytest
from fake_music import FakeMusic, FakePlaylist

from daily_mix_sync import cli, music_app
from daily_mix_sync.models import AppleCandidate
from daily_mix_sync.music_app import MusicApp

LIBRARY = [
    AppleCandidate("B2", "Dreams (Live)", "Fleetwood Mac", "The Dance", 279_000),
    AppleCandidate("A1", "Mr. Brightside", "The Killers", "Hot Fuss", 222_973),
    AppleCandidate("B1", "Dreams", "Fleetwood Mac", "Rumours", 257_800),
    AppleCandidate("C1", "Let It Go", "Idina Menzel", "Frozen (Original Motion Picture Soundtrack)", 223_840),
    AppleCandidate("E1", "Hotel California", "Eagles", "Hotel California", 391_376),
    AppleCandidate("F1", "Paper Planes", "Ines Moreau", "Atlas", 198_000),
    AppleCandidate("G1", "Harbor Lights", "Juno Vale", "Harbor Lights", 215_000),
    AppleCandidate("H1", "Night Signals", "Mara Lindqvist", "Night Signals", 201_000),
    AppleCandidate("I1", "Yellow Kite", "Cold Harbour", "Driftwood", 187_000),
]


def track(title, artist, album="", duration_ms=None, spotify_id=None):
    entry = {"title": title, "artist": artist, "spotify_track_id": spotify_id or f"id-{title}-{artist}"}
    if album:
        entry["album"] = album
    if duration_ms:
        entry["duration_ms"] = duration_ms
    return entry


BRIGHTSIDE = track("Mr. Brightside", "The Killers", "Hot Fuss", 222_000)
DREAMS = track("Dreams", "Fleetwood Mac", "Rumours", 257_000)
HOTEL = track("Hotel California - 2013 Remaster", "Eagles", "Hotel California (2013 Remaster)", 391_000)
PAPER = track("Paper Planes", "Ines Moreau", "Atlas", 198_000)
HARBOR = track("Harbor Lights", "Juno Vale", "Harbor Lights", 215_000)
SIGNALS = track("Night Signals", "Mara Lindqvist", "Night Signals", 201_000)
KITE = track("Yellow Kite", "Cold Harbour", "Driftwood", 187_000)
# Needs review: the closest library song is the plain "Let It Go".
LET_IT_GO = track(
    'Let It Go - From "Frozen"/Soundtrack Version', "Idina Menzel",
    "Frozen (Original Motion Picture Soundtrack)", 224_000,
)
ABSENT = track("Not There", "Nobody")
ALSO_ABSENT = track("Nowhere Song", "No One")

# Four mixes. Mr. Brightside, Paper Planes, Harbor Lights and Yellow Kite are each in two.
MIXES = {
    "Daily Mix 1": [BRIGHTSIDE, ABSENT, DREAMS, HOTEL],
    "Daily Mix 2": [PAPER, BRIGHTSIDE, HARBOR],
    "Daily Mix 3": [SIGNALS, LET_IT_GO, KITE],
    "Daily Mix 4": [HARBOR, PAPER, ALSO_ABSENT, KITE],
}
WRITTEN = {
    "Spotify Daily Mix 1": ["A1", "B1", "E1"],
    "Spotify Daily Mix 2": ["F1", "A1", "G1"],
    "Spotify Daily Mix 3": ["H1", "I1"],
    "Spotify Daily Mix 4": ["G1", "F1", "I1"],
}
LIKED = "Spotify Liked Songs"
OLD_MIX_2 = ["B2", "C1"]  # what "Spotify Daily Mix 2" holds before the sync


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """An in-memory Music with the user's own playlist and one Daily Mix already there."""
    fake = FakeMusic(
        library=LIBRARY,
        playlists=[
            FakePlaylist("LIKED00000000001", LIKED, ["A1", "B1"]),
            FakePlaylist("MIX2000000000001", "Spotify Daily Mix 2", list(OLD_MIX_2)),
        ],
    )
    monkeypatch.setattr(cli, "MusicApp", lambda prefix: MusicApp(prefix, run=fake))
    monkeypatch.setattr(cli, "_interactive", lambda: False)
    monkeypatch.chdir(tmp_path)
    return fake


@pytest.fixture
def exports(tmp_path):
    """Write the named mixes as export files and return their paths, in order."""

    def write(*names, mixes=MIXES):
        paths = []
        for name in names:
            path = tmp_path / f"{name.lower().replace(' ', '_')}.json"
            path.write_text(json.dumps({"playlist_name": name, "tracks": mixes[name]}), encoding="utf-8")
            paths.append(str(path))
        return paths

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


def run(capsys, *argv):
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def contents(fake):
    """What every playlist holds, by name."""
    return {playlist.name: list(playlist.track_ids) for playlist in fake.playlists}


def untouched(fake):
    """Nothing was created and nothing was changed."""
    return fake.changes() == [] and contents(fake) == {LIKED: ["A1", "B1"], "Spotify Daily Mix 2": OLD_MIX_2}


def searches(fake):
    return [args[0] for script, args in fake.calls if script is music_app._SEARCH]


def stored(tmp_path):
    with sqlite3.connect(tmp_path / "data" / "mappings.sqlite3") as conn:
        return {key: (track_id, method) for key, track_id, method in conn.execute(
            "SELECT source_key, music_persistent_id, method FROM mappings"
        )}


ALL_FOUR = ("Daily Mix 1", "Daily Mix 2", "Daily Mix 3", "Daily Mix 4")


# --- one export is still one export ---------------------------------------------------


def test_one_export_behaves_as_it_always_did(fake, exports, capsys, keyboard):
    prompts = keyboard("y")
    code, out, err = run(capsys, "sync", *exports("Daily Mix 1"), "--no-review")
    assert (code, err) == (0, "")
    assert out.startswith("Daily Mix 1\n-----------\n")
    assert prompts == ["\nCreate 'Spotify Daily Mix 1' with these 3 track(s)? [y/N] "]
    assert out.endswith("\n✓ Playlist updated and verified.\n")
    for batch_only in ("Matching", "Ready to update", "Batch", "Continue?", "Result"):
        assert batch_only not in out
    assert contents(fake)["Spotify Daily Mix 1"] == WRITTEN["Spotify Daily Mix 1"]


def test_one_export_into_a_named_playlist_still_works(fake, exports, capsys):
    code, _, _ = run(capsys, "sync", *exports("Daily Mix 1"), "--yes", "--into", "Spotify Daily Mix TEST")
    assert code == 0
    assert contents(fake)["Spotify Daily Mix TEST"] == ["A1", "B1", "E1"]
    assert "Spotify Daily Mix 1" not in contents(fake)


# --- several exports -----------------------------------------------------------------


def test_two_exports_each_go_to_their_own_playlist(fake, exports, capsys):
    code, out, err = run(capsys, "sync", *exports("Daily Mix 1", "Daily Mix 2"), "--yes")
    assert (code, err) == (0, "")
    assert contents(fake) == {
        LIKED: ["A1", "B1"],
        "Spotify Daily Mix 1": WRITTEN["Spotify Daily Mix 1"],
        "Spotify Daily Mix 2": WRITTEN["Spotify Daily Mix 2"],
    }
    assert "2 playlists updated successfully." in out


def test_four_exports_are_all_written_each_in_its_own_order(fake, exports, capsys):
    code, out, err = run(capsys, "sync", *exports(*ALL_FOUR), "--yes")
    assert (code, err) == (0, "")
    assert contents(fake) == {LIKED: ["A1", "B1"], **WRITTEN}
    assert out.rstrip().endswith("4 playlists updated successfully.")


def test_the_order_of_the_files_does_not_change_what_each_playlist_gets(fake, exports, capsys):
    code, _, _ = run(capsys, "sync", *reversed(exports(*ALL_FOUR)), "--yes")
    assert code == 0
    assert contents(fake) == {LIKED: ["A1", "B1"], **WRITTEN}


def test_a_song_in_several_mixes_is_written_to_each(fake, exports, capsys):
    run(capsys, "sync", *exports(*ALL_FOUR), "--yes")
    holding = [name for name, ids in contents(fake).items() if "F1" in ids]
    assert holding == ["Spotify Daily Mix 2", "Spotify Daily Mix 4"]


def test_an_existing_playlist_is_emptied_and_refilled_not_recreated(fake, exports, capsys):
    run(capsys, "sync", *exports("Daily Mix 1", "Daily Mix 2"), "--yes")
    assert fake.playlist("Spotify Daily Mix 2").persistent_id == "MIX2000000000001"
    assert music_app._DELETE_PLAYLIST not in fake.scripts_sent()


def test_only_the_destinations_are_changed(fake, exports, capsys):
    run(capsys, "sync", *exports(*ALL_FOUR), "--yes")
    targets = {
        args[0] if script is music_app._CREATE_PLAYLIST else args[1]
        for script, args in fake.changes()
    }
    assert targets == set(WRITTEN)
    assert fake.playlist(LIKED).track_ids == ["A1", "B1"]


# --- everything is settled before anything is written ------------------------------------


def test_every_track_is_matched_before_the_first_playlist_is_touched(fake, exports, capsys):
    run(capsys, "sync", *exports(*ALL_FOUR), "--yes")
    scripts = fake.scripts_sent()
    last_search = max(i for i, script in enumerate(scripts) if script is music_app._SEARCH)
    changing = (music_app._CREATE_PLAYLIST, music_app._CLEAR_PLAYLIST, music_app._ADD_TRACKS)
    first_change = min(i for i, script in enumerate(scripts) if script in changing)
    assert last_search < first_change


def test_a_malformed_second_file_means_nothing_is_written_or_even_searched(fake, exports, capsys, tmp_path):
    broken = tmp_path / "daily_mix_2.json"
    paths = exports("Daily Mix 1", "Daily Mix 3")
    broken.write_text("{ this is not json", encoding="utf-8")
    code, out, err = run(capsys, "sync", paths[0], str(broken), paths[1], "--yes")
    assert code == 1
    assert "daily_mix_2.json is not valid JSON" in err
    assert untouched(fake)
    assert searches(fake) == []


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (None, "cannot read"),
        ('{"playlist_name": "Daily Mix 9", "tracks": []}', "the tracks list is empty"),
        ('{"playlist_name": "Daily Mix 9"}', "expected an object like"),
        ('{"playlist_name": "Daily Mix 9", "tracks": [{"title": "No Artist"}]}', "none of its entries can be used"),
    ],
)
def test_any_unusable_file_stops_the_whole_batch_before_music_is_changed(
    fake, exports, capsys, tmp_path, content, message
):
    bad = tmp_path / "daily_mix_9.json"
    if content is not None:
        bad.write_text(content, encoding="utf-8")
    code, _, err = run(capsys, "sync", *exports("Daily Mix 1", "Daily Mix 2"), str(bad), "--yes")
    assert code == 1
    assert message in err and "daily_mix_9.json" in err
    assert untouched(fake)
    assert searches(fake) == []


def test_a_bad_config_stops_the_batch_before_anything(fake, exports, capsys, tmp_path):
    (tmp_path / "config.json").write_text('{"search_limit": 0}', encoding="utf-8")
    code, _, err = run(capsys, "sync", *exports("Daily Mix 1", "Daily Mix 2"), "--yes")
    assert code == 1 and "search_limit must be" in err
    assert fake.calls == []


def test_two_playlists_in_music_with_one_destination_name_stop_the_batch(fake, exports, capsys):
    fake.playlists.append(FakePlaylist("MIX3000000000001", "Spotify Daily Mix 3", ["A1"]))
    fake.playlists.append(FakePlaylist("MIX3000000000002", "Spotify Daily Mix 3", ["B1"]))
    code, _, err = run(capsys, "sync", *exports(*ALL_FOUR), "--yes")
    assert code == 1
    assert "Spotify Daily Mix 3" in err
    assert fake.changes() == []
    assert searches(fake) == [], "found out before any matching was done"


# --- two exports, one playlist ------------------------------------------------------------


def test_two_exports_for_one_playlist_are_refused(fake, exports, capsys, tmp_path):
    first, second = exports("Daily Mix 1", "Daily Mix 2")
    copy = tmp_path / "copy_of_mix_1.json"
    copy.write_text(json.dumps({"playlist_name": "Daily Mix 1", "tracks": MIXES["Daily Mix 3"]}), encoding="utf-8")
    code, out, err = run(capsys, "sync", first, second, str(copy), "--yes")
    assert code == 1
    assert "2 exports would be written to the same playlist, 'Spotify Daily Mix 1':" in err
    assert f"  {first}  (Daily Mix 1)" in err
    assert f"  {copy}  (Daily Mix 1)" in err
    assert second not in err, "the export that clashes with nothing is not named"
    assert "Nothing was changed." in err
    assert untouched(fake)
    assert searches(fake) == []


def test_the_same_file_given_twice_is_refused(fake, exports, capsys):
    (path,) = exports("Daily Mix 1")
    code, _, err = run(capsys, "sync", path, path, "--yes")
    assert code == 1 and "would be written to the same playlist" in err
    assert untouched(fake)


@pytest.mark.parametrize("other", ["daily mix 1", "Spotify Daily Mix 1", "DAILY MIX 1", "Spotify  Daily Mix   1"])
def test_names_that_come_to_the_same_playlist_clash_however_they_are_written(
    fake, exports, capsys, tmp_path, other
):
    (first,) = exports("Daily Mix 1")
    second = tmp_path / "other.json"
    second.write_text(json.dumps({"playlist_name": other, "tracks": MIXES["Daily Mix 2"]}), encoding="utf-8")
    code, _, err = run(capsys, "sync", first, str(second), "--yes")
    assert code == 1 and "would be written to the same playlist" in err
    assert untouched(fake)


def test_every_clash_is_reported_not_just_the_first(fake, exports, capsys, tmp_path):
    paths = exports("Daily Mix 1", "Daily Mix 2")
    for number in (1, 2):
        again = tmp_path / f"again_{number}.json"
        again.write_text(
            json.dumps({"playlist_name": f"Daily Mix {number}", "tracks": MIXES["Daily Mix 3"]}), encoding="utf-8"
        )
        paths.append(str(again))
    code, _, err = run(capsys, "sync", *paths, "--yes")
    assert code == 1
    assert err.count("would be written to the same playlist") == 2
    assert "'Spotify Daily Mix 1'" in err and "'Spotify Daily Mix 2'" in err


# --- --into ---------------------------------------------------------------------------------


def test_into_is_refused_with_several_exports(fake, exports, capsys):
    code, _, err = run(
        capsys, "sync", *exports("Daily Mix 1", "Daily Mix 2"), "--yes", "--into", "Spotify Daily Mix TEST"
    )
    assert code == 1
    assert "--into names a single playlist" in err and "2 exports" in err
    assert fake.calls == [], "refused before Music was asked anything"


def test_into_is_refused_with_several_exports_even_for_a_dry_run(fake, exports, capsys):
    code, _, err = run(
        capsys, "sync", *exports("Daily Mix 1", "Daily Mix 2"), "--dry-run", "--into", "Spotify Daily Mix TEST"
    )
    assert code == 1 and "--into names a single playlist" in err


# --- dry run ----------------------------------------------------------------------------------


def test_a_batch_dry_run_reports_everything_and_changes_nothing(fake, exports, capsys):
    code, out, err = run(capsys, "sync", *exports(*ALL_FOUR), "--dry-run")
    assert (code, err) == (0, "")
    assert untouched(fake)
    for name in ALL_FOUR:
        assert f"\n{name}\n-----------\n" in out
    assert "\nWould update:\n" in out
    assert "Spotify Daily Mix 1     3 tracks  (new playlist)\n" in out
    assert "Spotify Daily Mix 2     3 tracks  (currently 2)\n" in out
    assert "\nBatch dry run\n" in out
    assert "would be created" in out and "would be updated" in out
    assert out.endswith("\nNo changes made (--dry-run).\n")
    assert "Ready to update" not in out and "✓" not in out


def test_a_batch_dry_run_asks_nothing(fake, exports, capsys, keyboard):
    prompts = keyboard("y", "1", "y")
    code, _, _ = run(capsys, "sync", *exports(*ALL_FOUR), "--dry-run")
    assert code == 0
    assert prompts == []
    assert untouched(fake)


def test_a_batch_dry_run_remembers_the_matches_it_found(fake, exports, capsys, tmp_path):
    run(capsys, "sync", *exports("Daily Mix 1", "Daily Mix 2"), "--dry-run")
    assert {track_id for track_id, _ in stored(tmp_path).values()} == {"A1", "B1", "E1", "F1", "G1"}


# --- one question for the whole batch ------------------------------------------------------------


def test_the_batch_is_confirmed_once(fake, exports, capsys, keyboard):
    prompts = keyboard("y")
    code, out, _ = run(capsys, "sync", *exports(*ALL_FOUR), "--no-review")
    assert code == 0
    assert prompts == ["\nContinue? [y/N] "]
    assert "\nReady to update:\n\n" in out
    assert "Spotify Daily Mix 1     3 tracks  (new playlist)\n" in out
    assert "Spotify Daily Mix 2     3 tracks  (currently 2)\n" in out
    assert "Spotify Daily Mix 3     2 tracks  (new playlist)\n" in out
    assert "Spotify Daily Mix 4     3 tracks  (new playlist)\n" in out
    assert contents(fake) == {LIKED: ["A1", "B1"], **WRITTEN}


@pytest.mark.parametrize("answer", ["n", "", "no", "maybe", "yes please"])
def test_anything_but_yes_changes_nothing(fake, exports, capsys, keyboard, answer):
    prompts = keyboard(answer)
    code, out, _ = run(capsys, "sync", *exports(*ALL_FOUR), "--no-review")
    assert code == 1
    assert len(prompts) == 1
    assert "Nothing was changed." in out
    assert untouched(fake)


def test_end_of_input_at_the_question_counts_as_no(fake, exports, capsys, keyboard):
    keyboard()
    code, out, _ = run(capsys, "sync", *exports("Daily Mix 1", "Daily Mix 2"), "--no-review")
    assert code == 1 and "Nothing was changed." in out
    assert untouched(fake)


def test_yes_skips_the_question(fake, exports, capsys, keyboard):
    prompts = keyboard()
    code, _, _ = run(capsys, "sync", *exports(*ALL_FOUR), "--yes", "--no-review")
    assert code == 0
    assert prompts == []
    assert contents(fake) == {LIKED: ["A1", "B1"], **WRITTEN}


def test_with_nobody_to_ask_and_no_yes_nothing_is_changed(fake, exports, capsys):
    code, _, err = run(capsys, "sync", *exports(*ALL_FOUR))
    assert code == 1 and "not confirmed" in err
    assert untouched(fake)


# --- review -----------------------------------------------------------------------------------------


def test_review_says_which_mix_it_is_about_and_stores_the_choice(fake, exports, capsys, keyboard, tmp_path):
    prompts = keyboard("1", "y")
    code, out, _ = run(capsys, "sync", *exports(*ALL_FOUR))
    assert code == 0
    assert "\nDaily Mix 3 — needs review\n" in out
    assert out.index("Daily Mix 3 — needs review") < out.index("\nDaily Mix 1\n-----------")
    assert prompts == ["Selection: ", "\nContinue? [y/N] "]
    assert contents(fake)["Spotify Daily Mix 3"] == ["H1", "C1", "I1"]
    assert stored(tmp_path)[f"spotify:track:{LET_IT_GO['spotify_track_id']}"] == ("C1", "manual")
    assert "Manual matches:             1" in out


def test_a_track_in_two_mixes_is_asked_about_once_and_the_answer_used_in_both(
    fake, exports, capsys, keyboard
):
    mixes = {"Daily Mix 3": [SIGNALS, LET_IT_GO], "Daily Mix 4": [LET_IT_GO, KITE]}
    prompts = keyboard("1", "y")
    code, out, _ = run(capsys, "sync", *exports("Daily Mix 3", "Daily Mix 4", mixes=mixes))
    assert code == 0
    assert prompts.count("Selection: ") == 1
    assert out.count("— needs review") == 1
    assert contents(fake)["Spotify Daily Mix 3"] == ["H1", "C1"]
    assert contents(fake)["Spotify Daily Mix 4"] == ["C1", "I1"]


def test_a_track_skipped_once_is_not_brought_up_again_in_the_same_run(fake, exports, capsys, keyboard):
    mixes = {"Daily Mix 3": [SIGNALS, LET_IT_GO], "Daily Mix 4": [LET_IT_GO, KITE]}
    prompts = keyboard("s", "y")
    code, out, _ = run(capsys, "sync", *exports("Daily Mix 3", "Daily Mix 4", mixes=mixes))
    assert code == 0
    assert prompts.count("Selection: ") == 1
    assert contents(fake)["Spotify Daily Mix 3"] == ["H1"]
    assert contents(fake)["Spotify Daily Mix 4"] == ["I1"]
    assert out.count("1 track(s) need review and are left out.") == 2


def test_quitting_review_ends_the_questions_for_every_mix_but_not_the_run(fake, exports, capsys, keyboard):
    second = track('Let It Go - From "Frozen"/Soundtrack Version', "Idina Menzel", spotify_id="another-id")
    mixes = {"Daily Mix 3": [SIGNALS, LET_IT_GO], "Daily Mix 4": [second, KITE]}
    prompts = keyboard("q", "y")
    code, out, _ = run(capsys, "sync", *exports("Daily Mix 3", "Daily Mix 4", mixes=mixes))
    assert code == 0
    assert prompts == ["Selection: ", "\nContinue? [y/N] "]
    assert "Daily Mix 4 — needs review" not in out
    assert contents(fake)["Spotify Daily Mix 3"] == ["H1"]
    assert contents(fake)["Spotify Daily Mix 4"] == ["I1"]


def test_each_mix_with_something_to_review_gets_its_own_heading(fake, exports, capsys, keyboard):
    second = track('Let It Go - From "Frozen"/Soundtrack Version', "Idina Menzel", spotify_id="another-id")
    mixes = {"Daily Mix 3": [SIGNALS, LET_IT_GO], "Daily Mix 4": [second, KITE]}
    prompts = keyboard("1", "s", "y")
    code, out, _ = run(capsys, "sync", *exports("Daily Mix 3", "Daily Mix 4", mixes=mixes))
    assert code == 0
    assert out.index("Daily Mix 3 — needs review") < out.index("Daily Mix 4 — needs review")
    assert prompts.count("Selection: ") == 2
    assert contents(fake)["Spotify Daily Mix 3"] == ["H1", "C1"]
    assert contents(fake)["Spotify Daily Mix 4"] == ["I1"]


def test_no_review_applies_to_the_whole_batch(fake, exports, capsys, keyboard):
    prompts = keyboard("y")
    code, out, _ = run(capsys, "sync", *exports(*ALL_FOUR), "--no-review")
    assert code == 0
    assert "Selection: " not in prompts
    assert "needs review\n" not in out
    assert contents(fake)["Spotify Daily Mix 3"] == ["H1", "I1"]
    assert "1 track(s) need review and are left out. To decide them, run:" in out
    assert "daily_mix_3.json" in out.split("To decide them, run:")[1].splitlines()[1]


def test_yes_with_someone_present_still_offers_review(fake, exports, capsys, keyboard):
    prompts = keyboard("1")
    code, _, _ = run(capsys, "sync", *exports(*ALL_FOUR), "--yes")
    assert code == 0
    assert prompts == ["Selection: "]
    assert contents(fake)["Spotify Daily Mix 3"] == ["H1", "C1", "I1"]


# --- one mapping database ---------------------------------------------------------------------------


def test_a_song_in_two_mixes_is_searched_for_once(fake, exports, capsys):
    run(capsys, "sync", *exports(*ALL_FOUR), "--yes")
    looked_for = searches(fake)
    for term in ("mr brightside killers", "paper planes ines moreau", "harbor lights juno vale", "yellow kite cold harbour"):
        assert looked_for.count(term) == 1, term


def test_later_mixes_report_the_shared_songs_as_cached(fake, exports, capsys):
    _, out, _ = run(capsys, "sync", *exports("Daily Mix 1", "Daily Mix 2"), "--yes")
    second = out[out.index("\nDaily Mix 2\n-----------") :]
    assert "Cached library matches:     1\nNew library matches:        2\n" in second


def test_all_mixes_share_the_one_mapping_database(fake, exports, capsys, tmp_path):
    run(capsys, "sync", *exports(*ALL_FOUR), "--yes")
    assert sorted(path.name for path in (tmp_path / "data").iterdir()) == ["mappings.sqlite3"]
    assert {track_id for track_id, _ in stored(tmp_path).values()} == {"A1", "B1", "E1", "F1", "G1", "H1", "I1"}


def test_a_second_batch_is_served_from_the_cache(fake, exports, capsys):
    run(capsys, "sync", *exports(*ALL_FOUR), "--yes")
    fake.calls.clear()
    code, out, _ = run(capsys, "sync", *exports(*ALL_FOUR), "--yes")
    assert code == 0
    assert contents(fake) == {LIKED: ["A1", "B1"], **WRITTEN}
    # Only what was never matched is looked for again.
    assert set(searches(fake)) == {"not there nobody", "not there", "nowhere song no one", "nowhere song",
                                   "let it go idina menzel", "let it go"}
    assert "✓ updated" in out and "✓ created" not in out


def test_a_remembered_song_that_left_the_library_is_matched_again_in_a_batch(fake, exports, capsys):
    run(capsys, "sync", *exports("Daily Mix 1", "Daily Mix 2"), "--yes")
    del fake.library["A1"]
    for playlist in fake.playlists:
        playlist.track_ids = [track_id for track_id in playlist.track_ids if track_id != "A1"]
    code, out, _ = run(capsys, "sync", *exports("Daily Mix 1", "Daily Mix 2"), "--yes")
    assert code == 0
    assert contents(fake)["Spotify Daily Mix 1"] == ["B1", "E1"]
    assert contents(fake)["Spotify Daily Mix 2"] == ["F1", "G1"]
    assert "Stale mappings:" in out


# --- a mix with nothing in the library --------------------------------------------------------------


NOTHING = {"Daily Mix 5": [ABSENT, ALSO_ABSENT]}


def test_a_mix_with_nothing_in_the_library_is_left_alone_and_the_others_go_ahead(fake, exports, capsys):
    fake.playlists.append(FakePlaylist("MIX5000000000001", "Spotify Daily Mix 5", ["B2", "E1"]))
    paths = exports("Daily Mix 1", "Daily Mix 5", "Daily Mix 2", mixes={**MIXES, **NOTHING})
    code, out, err = run(capsys, "sync", *paths, "--yes")
    assert (code, err) == (0, "")
    assert contents(fake)["Spotify Daily Mix 5"] == ["B2", "E1"], "not emptied"
    assert contents(fake)["Spotify Daily Mix 1"] == WRITTEN["Spotify Daily Mix 1"]
    assert contents(fake)["Spotify Daily Mix 2"] == WRITTEN["Spotify Daily Mix 2"]
    assert "None of its 2 tracks are in your Music library, so 'Spotify Daily Mix 5' is left as it is." in out
    assert "\nLeft unchanged:\n\nSpotify Daily Mix 5  none of its 2 tracks are in your library\n" in out
    assert "Daily Mix 5        2           0   – left unchanged: nothing in library" in out
    assert "2 playlists updated successfully. 1 left unchanged: none of its tracks are in your library." in out
    assert all("Spotify Daily Mix 5" not in args for _, args in fake.changes())


def test_a_mix_with_nothing_in_the_library_does_not_create_its_playlist(fake, exports, capsys):
    paths = exports("Daily Mix 1", "Daily Mix 5", mixes={**MIXES, **NOTHING})
    run(capsys, "sync", *paths, "--yes")
    assert "Spotify Daily Mix 5" not in contents(fake)


def test_when_no_mix_has_anything_in_the_library_it_is_an_error_and_nothing_changes(fake, exports, capsys):
    mixes = {"Daily Mix 5": [ABSENT], "Daily Mix 6": [ALSO_ABSENT]}
    code, out, err = run(capsys, "sync", *exports("Daily Mix 5", "Daily Mix 6", mixes=mixes), "--yes")
    assert code == 1
    assert "none of the tracks in these 2 exports are in your Music library" in err
    assert untouched(fake)
    assert "Ready to update" not in out


# --- one playlist fails to write --------------------------------------------------------------------


def test_a_failed_write_is_put_back_and_the_other_playlists_still_get_written(fake, exports, capsys):
    fake.fail_on = {music_app._ADD_TRACKS: [2]}  # the second playlist's tracks cannot be added
    code, out, err = run(capsys, "sync", *exports(*ALL_FOUR), "--yes")
    assert code == 1
    assert contents(fake) == {
        LIKED: ["A1", "B1"],
        "Spotify Daily Mix 1": WRITTEN["Spotify Daily Mix 1"],
        "Spotify Daily Mix 2": OLD_MIX_2,  # as it was before the run
        "Spotify Daily Mix 3": WRITTEN["Spotify Daily Mix 3"],
        "Spotify Daily Mix 4": WRITTEN["Spotify Daily Mix 4"],
    }
    assert "ERROR: updating 'Spotify Daily Mix 2' failed" in err
    assert "✓ Its previous contents were restored (2 track(s))." in err
    assert "Spotify Daily Mix 1" not in err and "Spotify Daily Mix 3" not in err
    assert "\nBatch sync finished with errors\n" in out
    assert "Daily Mix 1        4           3   ✓ created" in out
    assert "Daily Mix 2        3           3   ✗ write failed; previous contents restored" in out
    assert "Daily Mix 3        3           2   ✓ created" in out
    assert "Daily Mix 4        4           3   ✓ created" in out
    assert out.rstrip().endswith("3 of 4 playlists updated. 1 failed; see the errors above.")


def test_a_failure_in_the_first_playlist_does_not_stop_the_rest(fake, exports, capsys):
    fake.fail_on = {music_app._CLEAR_PLAYLIST: [1]}
    code, _, err = run(capsys, "sync", *exports(*ALL_FOUR), "--yes")
    assert code == 1
    assert "ERROR: updating 'Spotify Daily Mix 1' failed" in err
    for name in ("Spotify Daily Mix 2", "Spotify Daily Mix 3", "Spotify Daily Mix 4"):
        assert contents(fake)[name] == WRITTEN[name]


def test_a_failure_in_the_last_playlist_leaves_the_earlier_ones_written(fake, exports, capsys):
    fake.fail_on = {music_app._ADD_TRACKS: [4]}
    code, out, _ = run(capsys, "sync", *exports(*ALL_FOUR), "--yes")
    assert code == 1
    for name in ("Spotify Daily Mix 1", "Spotify Daily Mix 2", "Spotify Daily Mix 3"):
        assert contents(fake)[name] == WRITTEN[name], "completed playlists are not rolled back"
    assert contents(fake)["Spotify Daily Mix 4"] == []
    assert "Daily Mix 4        4           3   ✗ write failed; previous contents restored" in out


def test_a_playlist_that_reads_back_wrong_is_put_back_and_the_rest_carry_on(fake, exports, capsys):
    fake.ignore_adds = True  # Music takes every request to add tracks and does nothing
    fake.playlists.append(FakePlaylist("MIX1000000000001", "Spotify Daily Mix 1", ["B2"]))
    code, out, err = run(capsys, "sync", *exports("Daily Mix 1", "Daily Mix 2"), "--yes")
    assert code == 1
    assert err.count("ERROR: updating") == 2
    assert out.count("✗ write failed") == 2
    assert "0 of 2 playlists updated. 2 failed; see the errors above." in out


def test_a_restore_that_also_fails_is_said_plainly_and_the_rest_carry_on(fake, exports, capsys):
    fake.fail_on = {music_app._ADD_TRACKS: [2, 3]}  # the write, then the attempt to put it back
    code, out, err = run(capsys, "sync", *exports(*ALL_FOUR), "--yes")
    assert code == 1
    assert "✗ Its previous contents could NOT be restored" in err
    assert "Manual intervention is required: 'Spotify Daily Mix 2'" in err
    assert "Dreams (Live) — Fleetwood Mac" in err, "what it held is listed"
    assert "Daily Mix 2        3           3   ✗ write failed; previous contents NOT restored" in out
    assert contents(fake)["Spotify Daily Mix 3"] == WRITTEN["Spotify Daily Mix 3"]
    assert contents(fake)["Spotify Daily Mix 4"] == WRITTEN["Spotify Daily Mix 4"]


def test_a_playlist_that_cannot_even_be_read_is_left_untouched_and_the_rest_carry_on(fake, exports, capsys):
    # Each write reads its playlist twice: before, to record it, and after, to check it.
    fake.fail_on = {music_app._PLAYLIST_TRACKS: [3]}  # the second playlist, before anything is changed
    code, out, err = run(capsys, "sync", *exports(*ALL_FOUR), "--yes")
    assert code == 1
    assert "ERROR: 'Spotify Daily Mix 2' was not updated:" in err
    assert contents(fake)["Spotify Daily Mix 2"] == OLD_MIX_2
    assert "Daily Mix 2        3           3   ✗ not updated; its contents were not touched" in out
    for name in ("Spotify Daily Mix 1", "Spotify Daily Mix 3", "Spotify Daily Mix 4"):
        assert contents(fake)[name] == WRITTEN[name]


def test_every_failure_is_reported_when_several_playlists_fail(fake, exports, capsys):
    fake.fail_on = {music_app._ADD_TRACKS: [1, 4]}  # first playlist; then, after its restore, the third
    code, out, err = run(capsys, "sync", *exports(*ALL_FOUR), "--yes")
    assert code == 1
    assert err.count("ERROR: updating") == 2
    assert "2 of 4 playlists updated. 2 failed; see the errors above." in out


def test_interrupting_a_write_restores_that_playlist_and_starts_no_other(fake, exports, capsys):
    add = fake._handlers[music_app._ADD_TRACKS]
    calls = []

    def interrupted_on_the_second(*args):
        calls.append(args)
        if len(calls) == 2:
            raise KeyboardInterrupt
        return add(*args)

    fake._handlers[music_app._ADD_TRACKS] = interrupted_on_the_second
    code, out, err = run(capsys, "sync", *exports(*ALL_FOUR), "--yes")
    assert code == 130
    assert contents(fake)["Spotify Daily Mix 1"] == WRITTEN["Spotify Daily Mix 1"]
    assert contents(fake)["Spotify Daily Mix 2"] == OLD_MIX_2, "the one in hand was put back"
    assert "Spotify Daily Mix 3" not in contents(fake) and "Spotify Daily Mix 4" not in contents(fake)
    assert "\nBatch sync interrupted\n" in out
    assert out.count("– not attempted: the run was interrupted") == 2
    assert "1 of 4 playlists updated. 1 failed; see the errors above. The run was interrupted" in out


def test_interrupting_between_writes_stops_without_touching_the_next(fake, exports, capsys):
    read = fake._handlers[music_app._PLAYLIST_TRACKS]
    calls = []

    def interrupted_on_the_third(*args):
        calls.append(args)
        if len(calls) == 3:  # the second playlist, as its contents are about to be recorded
            raise KeyboardInterrupt
        return read(*args)

    fake._handlers[music_app._PLAYLIST_TRACKS] = interrupted_on_the_third
    code, out, _ = run(capsys, "sync", *exports(*ALL_FOUR), "--yes")
    assert code == 130
    assert contents(fake)["Spotify Daily Mix 1"] == WRITTEN["Spotify Daily Mix 1"]
    assert contents(fake)["Spotify Daily Mix 2"] == OLD_MIX_2
    assert out.count("– not attempted: the run was interrupted") == 3
    assert "Spotify Daily Mix 3" not in contents(fake)


def test_interrupting_during_matching_changes_nothing(fake, exports, capsys, monkeypatch):
    calls = []
    real = cli.match_playlist

    def interrupted_on_the_second(*args, **kwargs):
        calls.append(args)
        if len(calls) == 2:
            raise KeyboardInterrupt
        return real(*args, **kwargs)

    monkeypatch.setattr(cli, "match_playlist", interrupted_on_the_second)
    code, _, err = run(capsys, "sync", *exports(*ALL_FOUR), "--yes")
    assert code == 130 and "interrupted" in err
    assert untouched(fake)


# --- the reports ------------------------------------------------------------------------------------


def test_each_mix_has_its_own_report_in_the_order_given_and_none_run_together(fake, exports, capsys):
    _, out, _ = run(capsys, "sync", *exports(*ALL_FOUR), "--yes")
    starts = [out.index(f"\n{name}\n-----------\n") for name in ALL_FOUR]
    assert starts == sorted(starts)
    plan = out.index("\nReady to update:\n")
    assert starts[-1] < plan, "every report is finished before the plan"
    for name, start, end in zip(ALL_FOUR, starts, [*starts[1:], plan]):
        report = out[start:end]
        assert f"\nDestination:\nSpotify {name}\n" in report
        assert report.count("Tracks in Spotify export:") == 1
        assert report.count("New tracks:") == 1
        for other in ALL_FOUR:
            if other != name:
                assert other not in report


def test_matching_progress_is_shown_once_per_mix_before_the_reports(fake, exports, capsys):
    _, out, _ = run(capsys, "sync", *exports(*ALL_FOUR), "--yes")
    assert out.startswith(
        "Matching 4 exports against your Music library:\n"
        "  Daily Mix 1: 3 of 4 in your library\n"
        "  Daily Mix 2: 3 of 3 in your library\n"
        "  Daily Mix 3: 2 of 3 in your library, 1 for review\n"
        "  Daily Mix 4: 3 of 4 in your library\n"
        "\nDaily Mix 1\n"
    )


def test_the_summary_adds_up(fake, exports, capsys):
    _, out, _ = run(capsys, "sync", *exports(*ALL_FOUR), "--yes")
    summary = out[out.index("\nBatch sync complete\n") :]
    assert summary == (
        "\nBatch sync complete\n"
        "\n"
        "Playlist     Spotify  In library   Result\n"
        "-------------------------------------------------------\n"
        "Daily Mix 1        4           3   ✓ created\n"
        "Daily Mix 2        3           3   ✓ updated\n"
        "Daily Mix 3        3           2   ✓ created\n"
        "Daily Mix 4        4           3   ✓ created\n"
        "\n"
        "Total Spotify tracks:      14\n"
        "Already in library:        11\n"
        "Intentionally left out:     2\n"
        "Waiting for review:         1\n"
        "\n"
        "4 playlists updated successfully.\n"
    )


def test_the_summary_keeps_songs_left_out_apart_from_playlists_that_failed(fake, exports, capsys):
    fake.fail_on = {music_app._ADD_TRACKS: [1]}
    code, out, err = run(capsys, "sync", *exports("Daily Mix 1", "Daily Mix 2"), "--yes")
    assert code == 1
    # A song that is not in the library is not an error, and is not counted as one.
    assert "Intentionally left out:     1\n" in out
    assert "Not There" not in err
    assert "1 of 2 playlists updated. 1 failed; see the errors above." in out
    # The playlist that failed still shows how many of its songs are in the library.
    assert "Daily Mix 1        4           3   ✗ write failed; previous contents restored" in out


def test_details_lists_the_matches_of_every_mix(fake, exports, capsys):
    _, out, _ = run(capsys, "sync", *exports("Daily Mix 1", "Daily Mix 2"), "--dry-run", "--details")
    assert out.count("\nMatched:\n") == 2
    assert "Paper Planes — Ines Moreau [Atlas] 3:18" in out


def test_long_playlist_names_keep_the_summary_in_columns(fake, exports, capsys):
    mixes = {"Daily Mix 1": MIXES["Daily Mix 1"], "A Much Longer Playlist Name": MIXES["Daily Mix 2"]}
    _, out, _ = run(capsys, "sync", *exports("Daily Mix 1", "A Much Longer Playlist Name", mixes=mixes), "--dry-run")
    assert "Playlist                     Spotify  In library   Result\n" in out
    assert "Daily Mix 1                        4           3   would be created\n" in out
    assert "A Much Longer Playlist Name        3           3   would be created\n" in out
    assert "Spotify Daily Mix A Much Longer Playlist Name     3 tracks  (new playlist)\n" in out


# --- still library-only -----------------------------------------------------------------------------


def test_a_batch_adds_nothing_to_the_library_and_uses_no_window_automation(fake, exports, capsys):
    before = dict(fake.library)
    code, _, _ = run(capsys, "sync", *exports(*ALL_FOUR), "--yes")
    assert code == 0
    assert fake.library == before
    for script in fake.scripts_sent():
        assert "System Events" not in script
        assert "Add to Library" not in script


def test_a_batch_only_ever_changes_managed_playlists(fake, exports, capsys):
    mixes = {"Liked Songs": MIXES["Daily Mix 1"], "Daily Mix 2": MIXES["Daily Mix 2"]}
    code, _, _ = run(capsys, "sync", *exports("Liked Songs", "Daily Mix 2", mixes=mixes), "--yes")
    assert code == 0
    assert contents(fake)[LIKED] == ["A1", "B1"], "the user's own playlist of a similar name is not the target"
    assert contents(fake)["Spotify Daily Mix Liked Songs"] == WRITTEN["Spotify Daily Mix 1"]
    for script, args in fake.changes():
        name = args[0] if script is music_app._CREATE_PLAYLIST else args[1]
        assert name.startswith("Spotify Daily Mix ")


def test_the_batch_follows_the_configured_prefix(fake, exports, capsys, tmp_path):
    (tmp_path / "config.json").write_text('{"managed_playlist_prefix": "From Spotify"}', encoding="utf-8")
    code, _, _ = run(capsys, "sync", *exports("Daily Mix 1", "Daily Mix 3"), "--yes", "--no-review")
    assert code == 0
    assert contents(fake)["From Spotify Daily Mix 1"] == WRITTEN["Spotify Daily Mix 1"]
    assert contents(fake)["From Spotify Daily Mix 3"] == WRITTEN["Spotify Daily Mix 3"]


def test_the_batch_uses_the_database_it_is_given(fake, exports, capsys, tmp_path):
    code, _, _ = run(capsys, "sync", *exports("Daily Mix 1", "Daily Mix 2"), "--yes", "--db", "elsewhere.sqlite3")
    assert code == 0
    assert (tmp_path / "elsewhere.sqlite3").exists()
    assert not (tmp_path / "data").exists()
