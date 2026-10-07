"""Tests that operate the real Music window through Accessibility.

Skipped unless pytest is run with --music-ui. They need Accessibility permission as
well as Automation permission for whatever app runs pytest, and Music comes to the
front while they run, so leave the Mac alone until they finish.

They change nothing: they search the Apple Music catalog, read results and menus,
and try "add" only on a song Music already shows as being in the library, where it
must do nothing. Adding a song that is not in the library is not tested here,
because it cannot be undone automatically; `music-catalog-add-test` does that by hand.
"""

import pytest

from daily_mix_sync.catalog import look_up, resolve_missing
from daily_mix_sync.config import Settings
from daily_mix_sync.database import MappingStore
from daily_mix_sync.importer import Playlist
from daily_mix_sync.matcher import MatchStatus
from daily_mix_sync.models import SourceTrack
from daily_mix_sync.music_app import run_osascript
from daily_mix_sync.music_ui import MusicCatalogUI
from daily_mix_sync.sync import match_playlist

pytestmark = pytest.mark.music_ui


@pytest.fixture(scope="module")
def ui(baseline):
    """The Music window, held in front for these tests and handed back afterwards."""
    ui = MusicCatalogUI()
    if not ui.accessibility_allowed():
        pytest.skip("Accessibility permission has not been granted to the app running pytest")
    with ui.session():
        yield ui


@pytest.fixture(scope="module")
def catalog_song(ui, songs):
    """A library song that the catalog search also finds, with its scored lookup."""
    for song in songs:
        source = SourceTrack(song.title, song.artist)
        lookup = look_up(source, ui, Settings())
        if lookup.match.status is MatchStatus.MATCHED:
            return song, lookup
    pytest.skip("none of the sampled library songs came back from an Apple Music search")


def test_the_window_has_everything_the_automation_relies_on(ui):
    report = ui.inspect()
    assert [item for state, item, _ in report if state == "missing"] == []
    items = {item for _, item, _ in report}
    assert {"main window", "sidebar", "search field", "search scope selector"} <= items


def test_catalog_search_returns_title_and_artist_for_a_known_song(catalog_song):
    song, lookup = catalog_song
    best = lookup.row(lookup.match.best)
    assert lookup.match.best.score >= 90
    assert best.title and best.artist
    assert best.catalog_id and best.catalog_id.isdigit()
    assert best.ordinal >= 1


def test_repeating_a_search_gives_the_same_rows(ui, catalog_song):
    song, lookup = catalog_song
    first = ui.search_catalog(f"{song.title} {song.artist}")
    again = ui.search_catalog(f"{song.title} {song.artist}")
    assert first and again == first


def test_a_nonsense_query_offers_nothing_acceptable(ui):
    lookup = look_up(SourceTrack("Zzqqxx Notarealsongtitle", "Nobody At All"), ui, Settings())
    assert lookup.match.status is MatchStatus.FAILED


def test_adding_a_song_music_already_has_changes_nothing(ui, music, catalog_song, baseline):
    song, lookup = catalog_song
    ui.search_catalog(f"{song.title} {song.artist}")  # put its rows back on screen
    rows = ui.search_catalog(f"{song.title} {song.artist}")
    present = next((row for row in rows[:4] if ui.in_library(row)), None)
    if present is None:
        pytest.skip("Music does not show any of the first results as being in the library")
    assert ui.add_to_library(present) is False
    assert music.library_size() == baseline["library size"]


def test_songs_already_in_the_library_never_bring_the_window_into_it(music, songs, tmp_path):
    sent = []

    def counting(script, args):
        sent.append(script)
        return run_osascript(script, args)

    quiet_ui = MusicCatalogUI(run=counting)
    playlist = Playlist(
        "Daily Mix TEST",
        tuple(SourceTrack(s.title, s.artist, s.album, s.duration_ms) for s in songs),
    )
    with MappingStore(tmp_path / "map.sqlite3") as store:
        results = match_playlist(playlist, music, store, Settings())
        assert all(r.status is MatchStatus.MATCHED for r in results)
        results, report = resolve_missing(results, music, quiet_ui, store, Settings())
    assert sent == [] and report.added == [] and report.problems == []
