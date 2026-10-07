"""The experimental catalog step, with stand-ins for the Music window and library.

Not part of normal sync: these cover catalog.py, which only the experimental-*
commands reach.
"""

import pytest
from fake_music import FakeMusic
from fake_ui import CatalogSong, FakeCatalogUI

from daily_mix_sync import catalog
from daily_mix_sync.catalog import QUIT, SKIP, look_up, resolve_missing, wait_for_library
from daily_mix_sync.config import Settings
from daily_mix_sync.database import MappingStore
from daily_mix_sync.importer import Playlist
from daily_mix_sync.matcher import MatchStatus
from daily_mix_sync.models import AUTO, MANUAL, AppleCandidate, SourceTrack
from daily_mix_sync.music_app import MusicApp
from daily_mix_sync.music_ui import MusicUIError, MusicUILayoutError
from daily_mix_sync.sync import match_playlist

DREAMS = AppleCandidate("B1", "Dreams", "Fleetwood Mac", "Rumours", 257_800)
CREEP_ACOUSTIC = AppleCandidate("D1", "Creep (Acoustic)", "Radiohead", "My Iron Lung - EP", 259_000)

CATALOG = [
    CatalogSong("901", "Mr. Brightside (Live)", "The Killers", "Live from the Royal Albert Hall", 250_000),
    CatalogSong("902", "Mr. Brightside", "Irish Made", "Covers", 220_000),
    CatalogSong("903", "Mr. Brightside", "The Killers", "Direct Hits", 223_973),
    CatalogSong("904", "Mr. Brightside", "The Killers", "Hot Fuss", 222_973),
    CatalogSong("905", "Let It Go", "Idina Menzel", "Frozen (Original Motion Picture Soundtrack)", 223_840),
    CatalogSong("906", "Creep", "Radiohead", "Pablo Honey", 238_640),
    CatalogSong("907", "Creep (Live)", "Radiohead", "Live Recordings", 260_000),
    CatalogSong("908", "X", "Zzz", "Whatever", 100_000),
]

WANT_DREAMS = SourceTrack("Dreams", "Fleetwood Mac", "Rumours", 257_000, "s1")
WANT_BRIGHTSIDE = SourceTrack("Mr. Brightside", "The Killers", "Hot Fuss", 222_000, "s2")
WANT_LET_IT_GO = SourceTrack('Let It Go - From "Frozen"', "Idina Menzel", "Frozen (Original Motion Picture Soundtrack)", 224_000, "s3")
WANT_CREEP = SourceTrack("Creep", "Radiohead", "Pablo Honey", 238_000, "s4")
WANT_NOTHING = SourceTrack("Not There", "Nobody", "", None, "s5")
PLAYLIST = Playlist(
    "Daily Mix 1", (WANT_DREAMS, WANT_BRIGHTSIDE, WANT_LET_IT_GO, WANT_CREEP, WANT_NOTHING)
)
SETTINGS = Settings(catalog_wait_s=5)


@pytest.fixture
def fake():
    return FakeMusic(library=[DREAMS, CREEP_ACOUSTIC])


@pytest.fixture
def music(fake):
    return MusicApp(run=fake)


@pytest.fixture
def ui(fake):
    return FakeCatalogUI(fake, CATALOG)


@pytest.fixture
def store(tmp_path):
    with MappingStore(tmp_path / "mappings.sqlite3") as store:
        yield store


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """Waiting costs nothing: sleeping just moves a pretend clock forward."""
    now = {"t": 0.0, "slept": 0.0}

    def sleep(seconds):
        now["t"] += seconds
        now["slept"] += seconds

    monkeypatch.setattr(catalog.time, "sleep", sleep)
    monkeypatch.setattr(catalog.time, "monotonic", lambda: now["t"])
    return now


@pytest.fixture
def local(music, store):
    """The playlist after ordinary library matching: only Dreams is found."""
    results = match_playlist(PLAYLIST, music, store, SETTINGS)
    assert [r.status for r in results] == [
        MatchStatus.MATCHED, MatchStatus.FAILED, MatchStatus.FAILED, MatchStatus.FAILED,
        MatchStatus.FAILED,
    ]
    return results


def by_title(results):
    return {r.track.title: r for r in results}


def added(report):
    """Titles of the tracks the catalog step added to the library and resolved."""
    return {track.title for track, _ in report.added}


def problems(report):
    return {track.title: reason for track, reason in report.problems}


# --- looking a track up ---------------------------------------------------------


def test_look_up_scores_catalog_rows_with_the_ordinary_matcher(ui):
    lookup = look_up(WANT_BRIGHTSIDE, ui, SETTINGS)
    assert ui.searches == ["mr brightside killers"]  # base title + primary artist
    assert lookup.match.status is MatchStatus.MATCHED
    ranked = [(lookup.row(c).catalog_id, c.score) for c in lookup.match.candidates]
    # Exact title and artist first, in page order. The cover and the live version
    # both fall below the review threshold.
    assert ranked[0] == ("903", 100) and ranked[1] == ("904", 100)
    assert dict(ranked[2:])["901"] == 70  # live: version mismatch
    assert dict(ranked[2:])["902"] < 75  # another artist
    best = lookup.match.best
    assert best.album_score is None and best.duration_score is None  # the page shows neither


def test_look_up_changes_nothing(ui, fake):
    look_up(WANT_BRIGHTSIDE, ui, SETTINGS)
    assert ui.added == [] and fake.changes() == [] and len(fake.library) == 2


def test_look_up_with_no_results(ui):
    lookup = look_up(SourceTrack("Qwertyuiop", "Asdfghjkl"), ui, SETTINGS)
    assert lookup.match.status is MatchStatus.FAILED and lookup.match.best is None


# --- waiting for the library ----------------------------------------------------


def test_wait_returns_as_soon_as_the_track_is_there(clock):
    answers = iter([None, None, DREAMS])
    assert wait_for_library(lambda: next(answers), timeout_s=30) == DREAMS
    assert clock["slept"] == 2  # two one-second waits, then found


def test_wait_gives_up_after_the_timeout(clock):
    calls = []
    assert wait_for_library(lambda: calls.append(1), timeout_s=5) is None
    assert clock["slept"] == 5 and len(calls) == 6  # checked once more at the deadline


def test_wait_does_not_sleep_when_the_track_is_already_there(clock):
    assert wait_for_library(lambda: DREAMS, timeout_s=30) == DREAMS
    assert clock["slept"] == 0


# --- the whole step -------------------------------------------------------------


def test_a_confident_catalog_match_is_added_validated_and_remembered(local, music, ui, store, fake):
    results, report = resolve_missing(local, music, ui, store, SETTINGS)
    brightside = by_title(results)["Mr. Brightside"]
    assert brightside.status is MatchStatus.MATCHED and "Mr. Brightside" in added(report)
    assert [r.catalog_id for r in ui.added][:1] == ["903"]  # first of the equal-scoring rows
    # What is used and remembered is the library track with its real persistent ID.
    assert brightside.chosen == AppleCandidate("LIB903", "Mr. Brightside", "The Killers", "Direct Hits", 223_973)
    mapping = store.get("spotify:track:s2")
    assert (mapping.persistent_id, mapping.method, mapping.score) == ("LIB903", AUTO, 100)
    assert brightside.mapping == mapping
    assert (WANT_BRIGHTSIDE, brightside.chosen) in report.added
    assert "LIB903" in fake.library


def test_tracks_the_library_already_supplied_are_never_looked_up(local, music, ui, store):
    resolve_missing(local, music, ui, store, SETTINGS)
    assert "dreams fleetwood mac" not in ui.searches
    assert ui.searches == [
        "mr brightside killers", "let it go idina menzel", "creep radiohead", "not there nobody",
    ]


def test_tracks_needing_local_review_are_not_sent_to_the_catalog(music, ui, store, fake):
    fake.library["L9"] = AppleCandidate(
        "L9", "Let It Go", "Idina Menzel", "Frozen (Original Motion Picture Soundtrack)", 223_840
    )
    results = match_playlist(PLAYLIST, music, store, SETTINGS)
    assert by_title(results)['Let It Go - From "Frozen"'].status is MatchStatus.REVIEW
    resolve_missing(results, music, ui, store, SETTINGS)
    assert "let it go idina menzel" not in ui.searches


def test_studio_version_is_fetched_when_the_library_only_has_another_version(local, music, ui, store):
    results, report = resolve_missing(local, music, ui, store, SETTINGS)
    creep = by_title(results)["Creep"]
    assert "Creep" in added(report) and creep.chosen.persistent_id == "LIB906"
    assert [r.catalog_id for r in ui.added if r.title.startswith("Creep")] == ["906"]


def test_no_matching_song_in_the_catalog_is_reported_not_guessed(local, music, ui, store):
    results, report = resolve_missing(local, music, ui, store, SETTINGS)
    nothing = by_title(results)["Not There"]
    assert nothing.status is MatchStatus.FAILED and "Not There" not in added(report)
    assert problems(report)["Not There"].startswith("no matching song in the catalog")
    assert store.get("spotify:track:s5") is None


def test_a_loose_catalog_guess_is_not_added(music, ui, store):
    source = SourceTrack("Zzqqxx", "Zzz", "", None, "s9")  # the catalog offers "X" by "Zzz"
    results = match_playlist(Playlist("Mix", (source,)), music, store, SETTINGS)
    results, report = resolve_missing(results, music, ui, store, SETTINGS)
    assert results[0].status is MatchStatus.FAILED and ui.added == []
    assert "closest was X by Zzz" in problems(report)["Zzqqxx"]


def test_ambiguous_catalog_results_without_anyone_to_ask_are_left_out(local, music, ui, store):
    results, report = resolve_missing(local, music, ui, store, SETTINGS)
    assert by_title(results)['Let It Go - From "Frozen"'].status is MatchStatus.FAILED
    assert problems(report)['Let It Go - From "Frozen"'] == (
        "the catalog's closest result needs review (Let It Go, score 80.0)"
    )
    assert all(r.title != "Let It Go" for r in ui.added)


def test_a_manual_catalog_choice_is_added_and_remembered_as_manual(local, music, ui, store):
    asked = []

    def choose(lookup):
        asked.append(lookup.track.title)
        return lookup.match.candidates[0]

    results, report = resolve_missing(local, music, ui, store, SETTINGS, choose=choose)
    assert asked == ['Let It Go - From "Frozen"']  # only the ambiguous one is asked about
    let_it_go = by_title(results)['Let It Go - From "Frozen"']
    assert let_it_go.status is MatchStatus.MANUAL
    assert 'Let It Go - From "Frozen"' in added(report)
    assert let_it_go.chosen.persistent_id == "LIB905"
    mapping = store.get("spotify:track:s3")
    assert (mapping.persistent_id, mapping.method, mapping.score) == ("LIB905", MANUAL, 80)


def test_a_manual_choice_of_a_different_version_is_honoured(music, ui, store):
    """Asked about a studio track, the person picks the live one on purpose."""
    source = SourceTrack("Creep - Unplugged Session", "Radiohead", "", None, "s8")
    results = match_playlist(Playlist("Mix", (source,)), music, store, SETTINGS)

    def choose(lookup):
        return next(c for c in lookup.match.candidates if lookup.row(c).title == "Creep (Live)")

    lookup = look_up(source, ui, SETTINGS)
    if lookup.match.status is not MatchStatus.REVIEW:
        pytest.skip("fixture no longer lands in the review band")
    results, _ = resolve_missing(results, music, ui, store, SETTINGS, choose=choose)
    assert results[0].status is MatchStatus.MANUAL
    assert results[0].chosen.title == "Creep (Live)"
    assert store.get("spotify:track:s8").method == MANUAL


def test_skipping_a_catalog_choice_adds_nothing(local, music, ui, store):
    results, report = resolve_missing(local, music, ui, store, SETTINGS, choose=lambda lookup: SKIP)
    assert by_title(results)['Let It Go - From "Frozen"'].status is MatchStatus.FAILED
    assert problems(report)['Let It Go - From "Frozen"'] == "catalog results were skipped in review"
    assert all(r.title != "Let It Go" for r in ui.added)
    assert store.get("spotify:track:s3") is None
    assert "Creep" in added(report)  # later tracks still handled


def test_quitting_the_catalog_review_stops_the_step_and_keeps_earlier_work(local, music, ui, store):
    results, report = resolve_missing(local, music, ui, store, SETTINGS, choose=lambda lookup: QUIT)
    assert report.stopped == "stopped at your request"
    assert added(report) == {"Mr. Brightside"}  # resolved before the question; Creep never reached
    assert "creep radiohead" not in ui.searches


def test_the_library_is_polled_until_the_added_song_shows_up(local, music, ui, store, clock):
    ui.appear_after = 4  # visible on the fourth library search after the add
    results, _ = resolve_missing(local, music, ui, store, SETTINGS)
    assert by_title(results)["Mr. Brightside"].chosen.persistent_id == "LIB903"
    assert clock["slept"] >= 3


def test_a_song_that_never_shows_up_times_out_and_is_not_remembered(local, music, ui, store, clock):
    ui.appear_after = 10_000
    results, report = resolve_missing(local, music, ui, store, SETTINGS)
    brightside = by_title(results)["Mr. Brightside"]
    assert brightside.status is MatchStatus.FAILED and brightside.chosen is None
    assert problems(report)["Mr. Brightside"] == (
        "Mr. Brightside by The Killers was added to the library, but no track matching it "
        "well enough appeared there within 5 seconds; it was left out, and the song stays "
        "in your library"
    )
    assert store.get("spotify:track:s2") is None
    assert report.added == [] or all(t.title != "Mr. Brightside" for t, _ in report.added)


def test_a_click_that_did_not_register_is_reported_as_such(local, music, ui, store):
    ui.clicks_register = False
    _, report = resolve_missing(local, music, ui, store, SETTINGS)
    assert problems(report)["Mr. Brightside"] == (
        "Add to Library was chosen for Mr. Brightside by The Killers, but Music still "
        "offers it; it was left out"
    )


def test_an_added_song_that_turns_out_not_to_match_is_not_used(local, music, ui, store):
    # The catalog row said "Mr. Brightside"; what arrives is a five-minute live cut.
    ui.arrives_as["903"] = AppleCandidate(
        "LIB903", "Mr. Brightside (Live)", "The Killers", "Live in Concert", 300_000
    )
    results, report = resolve_missing(local, music, ui, store, SETTINGS)
    brightside = by_title(results)["Mr. Brightside"]
    assert brightside.status is MatchStatus.FAILED and brightside.chosen is None
    assert "no track matching it well enough appeared" in problems(report)["Mr. Brightside"]
    assert store.get("spotify:track:s2") is None


def test_music_saying_the_song_is_already_there_adds_nothing_twice(local, music, ui, store, fake):
    ui.in_library_ids.add("903")  # Music's menu shows it as in the library...
    fake.library["LIB903"] = CATALOG[2].as_library_track()  # ...and it is
    del fake.library["LIB903"]  # but the library search cannot see it
    results, report = resolve_missing(local, music, ui, store, SETTINGS)
    assert all(r.catalog_id != "903" for r in ui.added)
    assert problems(report)["Mr. Brightside"].startswith(
        "Music shows Mr. Brightside by The Killers as already in the library"
    )


def test_add_to_library_not_being_offered_fails_that_track_only(local, music, ui, store):
    ui.fail_add = MusicUIError("The song result's menu offers neither Add to Library nor Delete from Library.")
    results, report = resolve_missing(local, music, ui, store, SETTINGS)
    assert added(report) == set()
    assert [r.status for r in results] == [r.status for r in local]
    assert "offers neither Add to Library" in problems(report)["Mr. Brightside"]
    assert "offers neither Add to Library" in problems(report)["Creep"]  # carried on to the next
    assert report.stopped == ""


def test_one_failed_search_does_not_stop_the_others(local, music, ui, store):
    ui.fail_search["mr brightside killers"] = MusicUIError("Apple Music showed no results within 15 seconds.")
    results, report = resolve_missing(local, music, ui, store, SETTINGS)
    assert "Mr. Brightside" not in added(report)
    assert problems(report)["Mr. Brightside"] == "Apple Music showed no results within 15 seconds."
    assert "Creep" in added(report)
    assert report.stopped == ""


def test_an_unexpected_window_layout_ends_the_step_before_more_is_tried(local, music, ui, store):
    ui.fail_search["let it go idina menzel"] = MusicUILayoutError("The search field was not found in Music's toolbar.")
    results, report = resolve_missing(local, music, ui, store, SETTINGS)
    assert report.stopped == "The search field was not found in Music's toolbar."
    assert added(report) == {"Mr. Brightside"}  # done before the failure; nothing after it
    assert ui.searches == ["mr brightside killers", "let it go idina menzel"]  # nothing after
    assert by_title(results)["Creep"].status is MatchStatus.FAILED


def test_dry_run_searches_but_adds_and_remembers_nothing(local, music, ui, store, fake):
    before = dict(fake.library)
    results, report = resolve_missing(local, music, ui, store, SETTINGS, dry_run=True)
    assert ui.added == [] and fake.library == before
    assert [r.status for r in results] == [r.status for r in local]
    assert [(t.title, row.title, row.catalog_id) for t, row in report.would_add] == [
        ("Mr. Brightside", "Mr. Brightside", "903"), ("Creep", "Creep", "906"),
    ]
    assert store.count() == 1  # only the earlier library match
    assert len(ui.searches) == 4


def test_once_remembered_a_track_never_needs_the_window_again(local, music, ui, store):
    resolve_missing(local, music, ui, store, SETTINGS)
    ui.searches.clear()
    ui.added.clear()

    later = match_playlist(PLAYLIST, music, store, SETTINGS)
    assert by_title(later)["Mr. Brightside"].status is MatchStatus.CACHED
    assert by_title(later)["Creep"].status is MatchStatus.CACHED
    later, report = resolve_missing(later, music, ui, store, SETTINGS)
    assert ui.added == []
    assert ui.searches == ["let it go idina menzel", "not there nobody"]  # still unresolved ones


def test_a_song_added_earlier_is_found_in_the_library_without_the_window(music, ui, store, fake):
    fake.library["LIB904"] = CATALOG[3].as_library_track()  # as if added on an earlier day
    results = match_playlist(Playlist("Mix", (WANT_BRIGHTSIDE,)), music, store, SETTINGS)
    assert results[0].status is MatchStatus.MATCHED
    _, report = resolve_missing(results, music, ui, store, SETTINGS)
    assert ui.searches == [] and ui.added == [] and report.added == []
