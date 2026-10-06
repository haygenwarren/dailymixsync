import sqlite3

import pytest

from daily_mix_sync.database import MappingStore
from daily_mix_sync.models import AUTO, MANUAL, AppleCandidate, SourceTrack
from daily_mix_sync.normalize import source_key

TRACK = SourceTrack("Song", "Artist", "Album", 200_000, spotify_track_id="abc")
FIRST = AppleCandidate("111", "Song", "Artist", "Album", 200_000)
SECOND = AppleCandidate("222", "Song (Live)", "Artist", "Live Album", 240_000)


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "nested" / "mappings.sqlite3"


@pytest.fixture
def store(db_path):
    with MappingStore(db_path) as store:
        yield store


def test_unknown_key_returns_none(store):
    assert store.get("spotify:track:nope") is None


def test_save_then_get(store):
    saved = store.save(TRACK, FIRST, 97.5, AUTO)
    assert saved == store.get("spotify:track:abc")
    assert (saved.persistent_id, saved.score, saved.method) == ("111", 97.5, AUTO)
    assert not saved.is_manual
    assert saved.matched_at.endswith("+00:00")


def test_track_without_spotify_id_is_stored_under_its_metadata_key(store):
    track = SourceTrack("Song", "Artist", "Album", 200_000)
    store.save(track, FIRST, 100, AUTO)
    assert store.get(source_key(track)).persistent_id == "111"
    assert store.get(source_key(SourceTrack("SONG", "artist", "Album", 200_000))) is not None


def test_mappings_survive_reopening(db_path):
    with MappingStore(db_path) as store:
        store.save(TRACK, FIRST, 100, AUTO)
    with MappingStore(db_path) as store:
        assert store.get("spotify:track:abc").persistent_id == "111"
        assert store.count() == 1


def test_automatic_match_never_replaces_a_manual_one(store):
    store.save(TRACK, SECOND, 67, MANUAL)
    on_record = store.save(TRACK, FIRST, 100, AUTO)
    assert (on_record.persistent_id, on_record.method, on_record.score) == ("222", MANUAL, 67)
    assert store.count() == 1


def test_manual_confirmation_replaces_an_automatic_match(store):
    store.save(TRACK, FIRST, 100, AUTO)
    on_record = store.save(TRACK, SECOND, 67, MANUAL)
    assert (on_record.persistent_id, on_record.method) == ("222", MANUAL)
    assert on_record.is_manual


def test_automatic_match_can_be_refreshed(store):
    store.save(TRACK, FIRST, 91, AUTO)
    assert store.save(TRACK, SECOND, 95, AUTO).persistent_id == "222"


def test_first_matched_date_is_kept_while_the_pairing_is_unchanged(store, db_path):
    store.save(TRACK, FIRST, 91, AUTO)
    with sqlite3.connect(db_path) as other:
        other.execute("UPDATE mappings SET matched_at = '2020-01-01T00:00:00+00:00'")

    confirmed = store.save(TRACK, FIRST, 91, MANUAL)
    assert confirmed.matched_at == "2020-01-01T00:00:00+00:00"
    assert confirmed.is_manual

    changed = store.save(TRACK, SECOND, 67, MANUAL)
    assert changed.matched_at != "2020-01-01T00:00:00+00:00"


def test_database_from_before_the_identifier_rename_is_upgraded_in_place(db_path):
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(db_path) as old:
        old.execute(
            "CREATE TABLE mappings (source_key TEXT PRIMARY KEY, apple_catalog_id TEXT NOT NULL,"
            " score REAL NOT NULL, method TEXT NOT NULL, matched_at TEXT NOT NULL,"
            " source_title TEXT NOT NULL, source_artist TEXT NOT NULL, apple_title TEXT NOT NULL,"
            " apple_artist TEXT NOT NULL, apple_album TEXT NOT NULL)"
        )
        old.execute(
            "INSERT INTO mappings VALUES ('spotify:track:abc', 'old-1', 91.5, 'manual',"
            " '2026-10-06T00:00:00+00:00', 'Song', 'Artist', 'Song', 'Artist', 'Album')"
        )

    with MappingStore(db_path) as store:
        kept = store.get("spotify:track:abc")
        assert (kept.persistent_id, kept.score, kept.method) == ("old-1", 91.5, MANUAL)
        assert store.save(TRACK, FIRST, 100, AUTO).persistent_id == "old-1"  # manual still wins
    with MappingStore(db_path) as store:  # opening again is a no-op
        assert store.count() == 1
    with sqlite3.connect(db_path) as check:
        columns = [row[1] for row in check.execute("PRAGMA table_info(mappings)")]
    assert "music_persistent_id" in columns and "apple_catalog_id" not in columns


def test_unknown_method_is_refused(store):
    with pytest.raises(ValueError):
        store.save(TRACK, FIRST, 100, "guessed")


def test_table_is_readable_on_its_own(store, db_path):
    store.save(TRACK, FIRST, 100, AUTO)
    with sqlite3.connect(db_path) as other:
        row = other.execute(
            "SELECT source_title, source_artist, apple_title, apple_artist, apple_album"
            " FROM mappings"
        ).fetchone()
    assert row == ("Song", "Artist", "Song", "Artist", "Album")
