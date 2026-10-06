import json

import pytest

from daily_mix_sync.apple_music import CatalogError, MockCatalog
from daily_mix_sync.models import AppleCandidate

SONGS = [
    AppleCandidate("1", "Dreams (Live)", "Fleetwood Mac", "The Dance"),
    AppleCandidate("2", "Dreams", "The Cranberries", "Everybody Else Is Doing It"),
    AppleCandidate("3", "Dreams", "Fleetwood Mac", "Rumours"),
    AppleCandidate("4", "Go Your Own Way", "Fleetwood Mac", "Rumours"),
    AppleCandidate("5", "Déjà Vu", "Beyoncé", "B'Day"),
]


def ids(results):
    return [song.catalog_id for song in results]


def test_search_returns_the_wrong_versions_too_in_fixture_order():
    catalog = MockCatalog(SONGS)
    assert ids(catalog.search_songs("dreams fleetwood mac")) == ["1", "3", "4"]


def test_search_ranks_fuller_matches_first():
    assert ids(MockCatalog(SONGS).search_songs("dreams cranberries")) == ["2", "1", "3"]


def test_search_ignores_case_and_accents():
    assert ids(MockCatalog(SONGS).search_songs("DEJA VU beyonce")) == ["5"]


def test_search_respects_the_limit():
    assert ids(MockCatalog(SONGS).search_songs("dreams fleetwood mac", limit=2)) == ["1", "3"]


@pytest.mark.parametrize("term", ["bohemian rhapsody queen", "", "   ", "!!!"])
def test_search_with_no_hits_returns_an_empty_list(term):
    assert MockCatalog(SONGS).search_songs(term) == []


def test_loads_the_sample_fixture(mock_catalog_path):
    catalog = MockCatalog.from_file(mock_catalog_path)
    hits = catalog.search_songs("hotel california eagles")
    assert ids(hits) == ["mock-5001", "mock-5002"]
    assert hits[1].duration_ms == 391376


def test_missing_fixture(tmp_path):
    with pytest.raises(CatalogError, match="cannot read mock catalog"):
        MockCatalog.from_file(tmp_path / "nope.json")


@pytest.mark.parametrize(
    "payload", ["{not json", json.dumps([1, 2]), json.dumps({"songs": [{"title": "No ID"}]})]
)
def test_malformed_fixture(tmp_path, payload):
    path = tmp_path / "catalog.json"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(CatalogError, match="malformed"):
        MockCatalog.from_file(path)
