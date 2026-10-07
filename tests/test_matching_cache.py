"""Stored mappings are used only while their track still exists."""

import logging

import pytest
from fake_music import FakeMusic

from daily_mix_sync.config import Settings
from daily_mix_sync.database import MappingStore
from daily_mix_sync.importer import Playlist
from daily_mix_sync.matcher import MatchStatus
from daily_mix_sync.models import AUTO, MANUAL, AppleCandidate, SourceTrack
from daily_mix_sync.music_app import MusicApp
from daily_mix_sync.normalize import source_key
from daily_mix_sync.sync import match_playlist

DREAMS = AppleCandidate("B1", "Dreams", "Fleetwood Mac", "Rumours", 257_800)
DREAMS_LIVE = AppleCandidate("B2", "Dreams (Live)", "Fleetwood Mac", "The Dance", 279_000)
BRIGHTSIDE = AppleCandidate("A1", "Mr. Brightside", "The Killers", "Hot Fuss", 222_973)

WANT_DREAMS = SourceTrack("Dreams", "Fleetwood Mac", "Rumours", 257_000, spotify_track_id="s1")
WANT_BRIGHTSIDE = SourceTrack("Mr. Brightside", "The Killers", "Hot Fuss", 222_000, "s2")
PLAYLIST = Playlist("Daily Mix 1", (WANT_DREAMS, WANT_BRIGHTSIDE))


@pytest.fixture
def fake():
    return FakeMusic(library=[DREAMS_LIVE, DREAMS, BRIGHTSIDE])


@pytest.fixture
def music(fake):
    return MusicApp(run=fake)


@pytest.fixture
def store(tmp_path):
    with MappingStore(tmp_path / "mappings.sqlite3") as store:
        yield store


def searches(fake):
    return [args[0] for script, args in fake.calls if "search library playlist" in script]


def test_the_music_library_serves_as_the_catalog(music, store):
    results = match_playlist(PLAYLIST, music, store, Settings())
    assert [(r.status, r.chosen) for r in results] == [
        (MatchStatus.MATCHED, DREAMS),
        (MatchStatus.MATCHED, BRIGHTSIDE),
    ]
    assert store.get("spotify:track:s1").persistent_id == "B1"


def test_valid_cache_hit_returns_the_live_track_without_searching(music, fake, store):
    match_playlist(PLAYLIST, music, store, Settings())
    fake.calls.clear()
    # The library entry is edited after it was cached; the live version is what counts.
    renamed = AppleCandidate("B1", "Dreams (2004 Remaster)", "Fleetwood Mac", "Rumours", 257_800)
    fake.library["B1"] = renamed

    results = match_playlist(PLAYLIST, music, store, Settings())
    assert [r.status for r in results] == [MatchStatus.CACHED, MatchStatus.CACHED]
    assert results[0].chosen == renamed
    assert results[0].mapping.persistent_id == "B1"
    assert not any(r.stale for r in results)
    assert searches(fake) == []


def test_stale_cache_entry_is_dropped_and_the_track_matched_again(music, fake, store, caplog):
    # Remembered: a track that has since left the library.
    gone = AppleCandidate("OLD9", "Dreams", "Fleetwood Mac", "Rumours", 257_800)
    store.save(WANT_DREAMS, gone, 100, AUTO)

    with caplog.at_level(logging.WARNING):
        results = match_playlist(PLAYLIST, music, store, Settings())
    dreams = results[0]
    assert dreams.status is MatchStatus.MATCHED
    assert dreams.stale is True
    assert dreams.chosen == DREAMS
    assert store.get("spotify:track:s1").persistent_id == "B1"  # replaced, not kept
    assert "remembered auto match (id OLD9) is no longer in the library" in caplog.text
    assert results[1].stale is False
    assert "dreams fleetwood mac" in searches(fake)


def test_stale_manual_mapping_is_dropped_too(music, store, caplog):
    gone = AppleCandidate("OLD9", "Dreams (Live)", "Fleetwood Mac", "Somewhere", 300_000)
    store.save(WANT_DREAMS, gone, 70, MANUAL)
    with caplog.at_level(logging.WARNING):
        results = match_playlist(PLAYLIST, music, store, Settings())
    assert results[0].status is MatchStatus.MATCHED and results[0].stale
    mapping = store.get("spotify:track:s1")
    assert (mapping.persistent_id, mapping.method) == ("B1", AUTO)
    assert "remembered manual match" in caplog.text


def test_stale_entry_with_nothing_left_to_match_is_forgotten(fake, music, store):
    store.save(WANT_DREAMS, DREAMS, 100, AUTO)
    del fake.library["B1"]
    del fake.library["B2"]

    results = match_playlist(PLAYLIST, music, store, Settings())
    assert results[0].status is MatchStatus.FAILED
    assert results[0].stale and results[0].chosen is None
    assert store.get("spotify:track:s1") is None
    assert results[1].status is MatchStatus.MATCHED


def test_stale_entry_can_end_up_needing_review(fake, music, store):
    store.save(WANT_DREAMS, DREAMS, 100, AUTO)
    del fake.library["B1"]
    fake.library["B3"] = AppleCandidate("B3", "Dreams (Sped Up)", "Fleetwood Mac", "Rumours", 257_800)
    del fake.library["B2"]

    results = match_playlist(PLAYLIST, music, store, Settings())
    assert results[0].status is MatchStatus.REVIEW
    assert results[0].stale and results[0].chosen is None
    assert store.get(source_key(WANT_DREAMS)) is None


def test_valid_manual_mapping_still_outranks_a_better_automatic_match(music, store):
    store.save(WANT_DREAMS, DREAMS_LIVE, 70, MANUAL)
    results = match_playlist(PLAYLIST, music, store, Settings())
    assert results[0].status is MatchStatus.CACHED
    assert results[0].chosen == DREAMS_LIVE
    assert store.get("spotify:track:s1").method == MANUAL


def test_mock_ids_cannot_survive_in_a_real_run(music, store):
    """If a made-up ID ever reached the real cache, validation throws it out."""
    store.save(WANT_DREAMS, AppleCandidate("mock-1102", "Dreams", "Fleetwood Mac"), 100, AUTO)
    results = match_playlist(PLAYLIST, music, store, Settings())
    assert results[0].chosen == DREAMS and results[0].stale
