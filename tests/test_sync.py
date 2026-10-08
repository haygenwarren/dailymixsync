"""End-to-end pipeline tests against the sample playlist and the mock catalog."""

import pytest

from daily_mix_sync.apple_music import MockCatalog
from daily_mix_sync.config import Settings
from daily_mix_sync.database import MappingStore
from daily_mix_sync.importer import load_playlist
from daily_mix_sync.matcher import MatchStatus
from daily_mix_sync.models import AUTO, MANUAL, AppleCandidate, SourceTrack
from daily_mix_sync.normalize import source_key
from daily_mix_sync.sync import match_playlist, search_term

# What a correct first run must decide for each sample track.
EXPECTED = {
    "Mr. Brightside": (MatchStatus.MATCHED, "mock-1003"),
    "Bohemian Rhapsody - Remastered 2011": (MatchStatus.MATCHED, "mock-2003"),
    "This Is What You Came For": (MatchStatus.MATCHED, "mock-3002"),
    "Déjà Vu": (MatchStatus.MATCHED, "mock-4002"),
    "Hotel California - 2013 Remaster": (MatchStatus.MATCHED, "mock-5002"),
    "Everlong - Acoustic Version": (MatchStatus.MATCHED, "mock-6002"),
    "Killing In The Name": (MatchStatus.MATCHED, "mock-7001"),
    'Let It Go - From "Frozen"/Soundtrack Version': (MatchStatus.REVIEW, "mock-8002"),
    "Creep": (MatchStatus.FAILED, "mock-9001"),
    "A Song Apple Music Does Not Have": (MatchStatus.FAILED, None),
    "Dreams": (MatchStatus.MATCHED, "mock-1102"),
}
MATCHED = sum(status is MatchStatus.MATCHED for status, _ in EXPECTED.values())


class CountingCatalog:
    def __init__(self, inner):
        self.inner = inner
        self.terms: list[str] = []  # title-and-artist searches
        self.title_terms: list[str] = []  # the broader, title-only searches

    def search_songs(self, term, limit, titles_only=False):
        (self.title_terms if titles_only else self.terms).append(term)
        return self.inner.search_songs(term, limit, titles_only=titles_only)

    def get_track(self, persistent_id):
        return self.inner.get_track(persistent_id)


@pytest.fixture
def playlist(sample_playlist_path):
    return load_playlist(sample_playlist_path)


@pytest.fixture
def catalog(mock_catalog_path):
    return CountingCatalog(MockCatalog.from_file(mock_catalog_path))


@pytest.fixture
def store(tmp_path):
    with MappingStore(tmp_path / "mappings.sqlite3") as store:
        yield store


def test_first_run_picks_the_right_candidate_for_every_sample_track(playlist, catalog, store):
    results = match_playlist(playlist, catalog, store, Settings())
    decided = {
        r.track.title: (r.status, r.best.candidate.persistent_id if r.best else None)
        for r in results
    }
    assert decided == EXPECTED
    assert [r.track for r in results] == list(playlist.tracks)  # playlist order kept


def test_only_automatic_matches_are_stored(playlist, catalog, store):
    results = match_playlist(playlist, catalog, store, Settings())
    assert store.count() == MATCHED
    for result in results:
        mapping = store.get(source_key(result.track))
        if result.status is MatchStatus.MATCHED:
            assert mapping.persistent_id == result.best.candidate.persistent_id
            assert mapping.score == result.best.score
            assert mapping.method == AUTO
        else:
            assert mapping is None


def test_second_run_reuses_stored_mappings_without_searching(playlist, catalog, store):
    match_playlist(playlist, catalog, store, Settings())
    assert len(catalog.terms) == len(playlist.tracks)
    # The broader search ran only for the three tracks the first search did not settle.
    assert len(catalog.title_terms) == len(playlist.tracks) - MATCHED
    catalog.terms.clear()
    catalog.title_terms.clear()

    results = match_playlist(playlist, catalog, store, Settings())
    statuses = [r.status for r in results]
    assert statuses.count(MatchStatus.CACHED) == MATCHED
    assert statuses.count(MatchStatus.MATCHED) == 0
    assert statuses.count(MatchStatus.REVIEW) == 1
    assert statuses.count(MatchStatus.FAILED) == 2
    # Only the three unresolved tracks are searched again, each in both ways.
    assert len(catalog.terms) == len(playlist.tracks) - MATCHED
    assert len(catalog.title_terms) == len(playlist.tracks) - MATCHED
    for result in results:
        if result.status is MatchStatus.CACHED:
            assert result.mapping.persistent_id == EXPECTED[result.track.title][1]


def test_manual_mapping_takes_priority_and_is_left_alone(playlist, catalog, store):
    creep = next(t for t in playlist.tracks if t.title == "Creep")
    chosen = AppleCandidate("mock-9001", "Creep (Acoustic)", "Radiohead", "My Iron Lung - EP")
    store.save(creep, chosen, 52.6, MANUAL)

    results = match_playlist(playlist, catalog, store, Settings())
    result = next(r for r in results if r.track.title == "Creep")
    assert result.status is MatchStatus.CACHED
    assert result.mapping.persistent_id == "mock-9001"
    assert result.mapping.is_manual
    assert "creep radiohead" not in catalog.terms
    assert "creep" not in catalog.title_terms
    assert store.get(source_key(creep)).method == MANUAL


def test_each_search_is_given_its_own_limit(playlist, store):
    seen = []

    class Recorder:
        def search_songs(self, term, limit, titles_only=False):
            seen.append((limit, titles_only))
            return []

        def get_track(self, persistent_id):
            return None

    match_playlist(playlist, Recorder(), store, Settings(search_limit=3, title_search_limit=7))
    assert set(seen) == {(3, False), (7, True)}
    assert seen.count((3, False)) == seen.count((7, True)) == len(playlist.tracks)


@pytest.mark.parametrize(
    ("title", "artist", "term"),
    [
        ("Bohemian Rhapsody - Remastered 2011", "Queen", "bohemian rhapsody queen"),
        ("This Is What You Came For", "Calvin Harris, Rihanna", "this is what you came for calvin harris"),
        ("Déjà Vu (feat. JAY Z)", "Beyoncé", "deja vu beyonce"),
        ("Everlong - Acoustic Version", "Foo Fighters", "everlong foo fighters"),
    ],
)
def test_search_term_is_base_title_plus_primary_artist(title, artist, term):
    assert search_term(SourceTrack(title, artist)) == term
