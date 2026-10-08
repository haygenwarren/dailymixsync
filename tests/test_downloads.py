"""Finding exports in a Downloads folder: what is one, which is newest, what is left out.

Everything here works on temporary folders. Nothing reads the real ~/Downloads, and
nothing talks to Music.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from daily_mix_sync import downloads
from daily_mix_sync.downloads import DownloadsError, discover, export_slug

PREFIX = "Spotify Daily Mix"
NOW = datetime(2026, 10, 8, 13, 30, tzinfo=UTC)
SOURCE = "https://open.spotify.com/playlist/37i9dQZF1E35FIXTURE0001"
TRACK = {
    "title": "Mr. Brightside", "artist": "The Killers", "album": "Hot Fuss", "duration_ms": 222000,
    "spotify_track_id": "FIXTURE000000000000001",
    "spotify_url": "https://open.spotify.com/track/FIXTURE000000000000001",
}
MISSING = object()


def ago(**delta) -> datetime:
    return NOW - timedelta(**delta)


def stamp(moment: datetime) -> str:
    """A time the way the extension writes it."""
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def touch(path: Path, moment: datetime) -> None:
    os.utime(path, (moment.timestamp(), moment.timestamp()))


def export(
    folder: Path, filename: str, name: object = "Daily Mix 1", *, exported: object = MISSING,
    modified: datetime | None = None, tracks: object = MISSING, source: object = SOURCE,
) -> Path:
    """Write an export file. `exported` is a datetime, a raw value, or MISSING for none."""
    data: dict = {}
    if name is not MISSING:
        data["playlist_name"] = name
    if source is not MISSING:
        data["source_url"] = source
    if exported is not MISSING:
        data["exported_at"] = stamp(exported) if isinstance(exported, datetime) else exported
    data["tracks"] = [TRACK] if tracks is MISSING else tracks
    path = folder / filename
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    when = modified or (exported if isinstance(exported, datetime) else ago(minutes=10))
    touch(path, when)
    return path


def find(folder: Path, max_age_hours: float = 24, prefix: str = PREFIX):
    return discover(folder, prefix, max_age_hours, NOW)


def names(exports) -> list[str]:
    return [e.path.name for e in exports]


# --- the folder ---------------------------------------------------------------------


def test_an_empty_folder_has_nothing(tmp_path):
    found = find(tmp_path)
    assert (found.selected, found.older, found.stale, found.broken, found.unrelated) == ([], [], [], [], 0)
    assert found.paths == []
    assert found.directory == tmp_path


def test_a_folder_that_is_not_there_is_an_error(tmp_path):
    with pytest.raises(DownloadsError, match="cannot read .*nowhere"):
        find(tmp_path / "nowhere")


def test_a_file_where_the_folder_should_be_is_an_error(tmp_path):
    (tmp_path / "Downloads").write_text("not a folder")
    with pytest.raises(DownloadsError, match="cannot read"):
        find(tmp_path / "Downloads")


def test_the_home_folder_is_resolved_not_spelled_out(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "Downloads").mkdir()
    export(tmp_path / "Downloads", "daily_mix_1.json", exported=ago(minutes=5))
    found = find(Path("~/Downloads"))
    assert found.directory == tmp_path / "Downloads"
    assert names(found.selected) == ["daily_mix_1.json"]


def test_only_the_folder_itself_is_looked_in(tmp_path):
    (tmp_path / "older").mkdir()
    export(tmp_path / "older", "daily_mix_1.json", exported=ago(minutes=5))
    assert find(tmp_path).selected == []


# --- one export ---------------------------------------------------------------------


def test_one_export_is_found_with_its_playlist_and_destination(tmp_path):
    path = export(tmp_path, "daily_mix_1.json", exported=ago(minutes=15), tracks=[TRACK, {**TRACK, "spotify_track_id": "X2", "title": "Other"}])
    (only,) = find(tmp_path).selected
    assert only.path == path
    assert only.playlist_name == "Daily Mix 1"
    assert only.destination == "Spotify Daily Mix 1"
    assert only.track_count == 2
    assert only.when == ago(minutes=15)
    assert only.by_file_time is False


def test_the_destination_follows_the_configured_prefix(tmp_path):
    export(tmp_path, "daily_mix_1.json", exported=ago(minutes=5))
    (only,) = find(tmp_path, prefix="From Spotify").selected
    assert only.destination == "From Spotify Daily Mix 1"


def test_an_export_is_known_by_what_is_in_it_not_by_its_name(tmp_path):
    export(tmp_path, "something else entirely.json", "Daily Mix 3", exported=ago(minutes=5))
    (only,) = find(tmp_path).selected
    assert (only.playlist_name, only.destination) == ("Daily Mix 3", "Spotify Daily Mix 3")


def test_the_extension_does_not_matter_for_case(tmp_path):
    export(tmp_path, "DAILY_MIX_1.JSON", exported=ago(minutes=5))
    assert names(find(tmp_path).selected) == ["DAILY_MIX_1.JSON"]


def test_a_hand_made_export_with_spotify_ids_counts(tmp_path):
    export(tmp_path, "mine.json", "Daily Mix 2", source=MISSING, modified=ago(minutes=3))
    (only,) = find(tmp_path).selected
    assert only.playlist_name == "Daily Mix 2"
    assert only.by_file_time is True


def test_an_export_with_the_source_address_but_no_track_ids_counts(tmp_path):
    export(tmp_path, "mix.json", exported=ago(minutes=5), tracks=[{"title": "Song", "artist": "Someone"}])
    assert names(find(tmp_path).selected) == ["mix.json"]


# --- several mixes ---------------------------------------------------------------------


def test_several_mixes_are_all_found_in_number_order(tmp_path):
    for number in (10, 2, 1, 4):
        export(tmp_path, f"daily_mix_{number}.json", f"Daily Mix {number}", exported=ago(minutes=number))
    found = find(tmp_path)
    assert [e.playlist_name for e in found.selected] == ["Daily Mix 1", "Daily Mix 2", "Daily Mix 4", "Daily Mix 10"]
    assert found.older == []
    assert found.paths == [tmp_path / f"daily_mix_{n}.json" for n in (1, 2, 4, 10)]


# --- several exports of one mix -----------------------------------------------------------


def test_the_newest_export_of_a_mix_is_the_one_selected(tmp_path):
    export(tmp_path, "daily_mix_1.json", exported=ago(hours=5, minutes=28))
    export(tmp_path, "daily_mix_1 (1).json", exported=ago(minutes=15))
    export(tmp_path, "daily_mix_2.json", "Daily Mix 2", exported=ago(minutes=14))
    found = find(tmp_path)
    assert names(found.selected) == ["daily_mix_1 (1).json", "daily_mix_2.json"]
    assert names(found.older) == ["daily_mix_1.json"]
    assert found.broken == [] and found.stale == []


def test_the_export_time_decides_not_the_file_time(tmp_path):
    # The earlier export was copied or touched later; it is still the earlier export.
    export(tmp_path, "daily_mix_1.json", exported=ago(hours=6), modified=ago(minutes=1))
    export(tmp_path, "daily_mix_1 (1).json", exported=ago(minutes=30), modified=ago(hours=3))
    assert names(find(tmp_path).selected) == ["daily_mix_1 (1).json"]


def test_the_number_chrome_adds_does_not_decide(tmp_path):
    export(tmp_path, "daily_mix_1 (2).json", exported=ago(hours=3))
    export(tmp_path, "daily_mix_1 (1).json", exported=ago(hours=1))
    export(tmp_path, "daily_mix_1.json", exported=ago(minutes=2))
    found = find(tmp_path)
    assert names(found.selected) == ["daily_mix_1.json"]
    assert names(found.older) == ["daily_mix_1 (2).json", "daily_mix_1 (1).json"]


def test_many_copies_of_one_mix_still_give_one(tmp_path):
    for copy in range(1, 8):
        export(tmp_path, f"daily_mix_1 ({copy}).json", exported=ago(minutes=100 - copy))
    found = find(tmp_path)
    assert names(found.selected) == ["daily_mix_1 (7).json"]
    assert len(found.older) == 6


def test_at_exactly_the_same_time_the_browsers_numbering_decides(tmp_path):
    # "name (2).json" was downloaded after "name (1).json", which came after "name.json".
    for filename in ("daily_mix_1 (10).json", "daily_mix_1.json", "daily_mix_1 (2).json", "daily_mix_1 (1).json"):
        export(tmp_path, filename, exported=ago(minutes=5), modified=ago(minutes=5))
    found = find(tmp_path)
    assert names(found.selected) == ["daily_mix_1 (10).json"]
    assert len(found.older) == 3


@pytest.mark.parametrize("other", ["Spotify Daily Mix 1", "daily mix 1", "DAILY MIX 1"])
def test_two_names_for_the_same_playlist_are_one_destination(tmp_path, other):
    export(tmp_path, "a.json", "Daily Mix 1", exported=ago(hours=2))
    export(tmp_path, "b.json", other, exported=ago(minutes=5))
    found = find(tmp_path)
    assert names(found.selected) == ["b.json"]
    assert names(found.older) == ["a.json"]


# --- when the export does not say when ------------------------------------------------------


def test_without_an_export_time_the_file_time_is_used(tmp_path):
    export(tmp_path, "daily_mix_1.json", modified=ago(hours=2))
    export(tmp_path, "daily_mix_1 (1).json", modified=ago(minutes=20))
    found = find(tmp_path)
    (only,) = found.selected
    assert only.path.name == "daily_mix_1 (1).json"
    assert only.by_file_time is True
    assert only.when == ago(minutes=20)


@pytest.mark.parametrize(
    "value",
    ["yesterday", "", "2026-13-45T99:00:00Z", 1759930200, None, ["2026-10-08T13:00:00Z"], "2026-10-08T13:00:00"],
)
def test_an_export_time_that_cannot_be_believed_is_not_used(tmp_path, value):
    export(tmp_path, "daily_mix_1.json", exported=value, modified=ago(minutes=40))
    (only,) = find(tmp_path).selected
    assert only.by_file_time is True
    assert only.when == ago(minutes=40)


def test_an_export_time_in_the_future_is_not_believed(tmp_path):
    export(tmp_path, "daily_mix_1.json", exported=NOW + timedelta(days=3), modified=ago(minutes=40))
    (only,) = find(tmp_path).selected
    assert only.by_file_time is True and only.when == ago(minutes=40)


def test_a_clock_a_few_minutes_ahead_is_tolerated(tmp_path):
    export(tmp_path, "daily_mix_1.json", exported=NOW + timedelta(minutes=2), modified=ago(minutes=40))
    (only,) = find(tmp_path).selected
    assert only.by_file_time is False


def test_export_time_and_file_time_are_compared_as_moments(tmp_path):
    export(tmp_path, "with_time.json", exported=ago(hours=1))
    export(tmp_path, "without_time.json", modified=ago(minutes=10))
    assert names(find(tmp_path).selected) == ["without_time.json"]
    export(tmp_path, "with_time.json", exported=ago(minutes=1))
    assert names(find(tmp_path).selected) == ["with_time.json"]


def test_export_times_in_other_time_zones_are_understood(tmp_path):
    export(tmp_path, "a.json", exported="2026-10-08T09:00:00-04:00")  # 13:00 UTC
    export(tmp_path, "b.json", exported="2026-10-08T12:30:00Z")
    assert names(find(tmp_path).selected) == ["a.json"]


# --- exports that are too old ------------------------------------------------------------------


def test_an_export_older_than_a_day_is_left_out_and_reported(tmp_path):
    export(tmp_path, "daily_mix_1.json", exported=ago(minutes=30))
    export(tmp_path, "daily_mix_3.json", "Daily Mix 3", exported=ago(days=3, hours=4))
    found = find(tmp_path)
    assert names(found.selected) == ["daily_mix_1.json"]
    assert names(found.stale) == ["daily_mix_3.json"]
    assert found.max_age_hours == 24


def test_the_limit_can_be_changed(tmp_path):
    export(tmp_path, "daily_mix_3.json", "Daily Mix 3", exported=ago(hours=30))
    assert names(find(tmp_path, max_age_hours=24).stale) == ["daily_mix_3.json"]
    assert names(find(tmp_path, max_age_hours=48).selected) == ["daily_mix_3.json"]
    assert names(find(tmp_path, max_age_hours=0.5).stale) == ["daily_mix_3.json"]


def test_an_export_exactly_at_the_limit_is_still_used(tmp_path):
    export(tmp_path, "edge.json", exported=ago(hours=24))
    export(tmp_path, "over.json", "Daily Mix 2", exported=ago(hours=24, seconds=1))
    found = find(tmp_path)
    assert names(found.selected) == ["edge.json"]
    assert names(found.stale) == ["over.json"]


def test_only_the_newest_export_of_a_mix_is_reported_as_too_old(tmp_path):
    export(tmp_path, "daily_mix_1.json", exported=ago(days=9))
    export(tmp_path, "daily_mix_1 (1).json", exported=ago(days=2))
    found = find(tmp_path)
    assert found.selected == []
    assert names(found.stale) == ["daily_mix_1 (1).json"]
    assert names(found.older) == ["daily_mix_1.json"]


def test_old_copies_of_a_mix_with_a_recent_export_are_just_older_copies(tmp_path):
    export(tmp_path, "daily_mix_1.json", exported=ago(days=9))
    export(tmp_path, "daily_mix_1 (1).json", exported=ago(minutes=5))
    found = find(tmp_path)
    assert names(found.selected) == ["daily_mix_1 (1).json"]
    assert found.stale == []
    assert names(found.older) == ["daily_mix_1.json"]


def test_staleness_is_judged_by_the_file_time_when_there_is_no_export_time(tmp_path):
    export(tmp_path, "old.json", modified=ago(days=2))
    assert names(find(tmp_path).stale) == ["old.json"]


# --- files that are not ours ----------------------------------------------------------------


@pytest.mark.parametrize(
    "content",
    [
        '{"name": "some-package", "version": "1.0.0"}',
        "[1, 2, 3]",
        '"just a string"',
        "42",
        "null",
        "{}",
        '{"tracks": []}',
        '{"playlist_name": "Daily Mix 1"}',
        # A name and tracks, but nothing that says Spotify.
        '{"playlist_name": "Road Trip", "tracks": [{"title": "Song", "artist": "Someone"}]}',
        '{"playlist_name": "Road Trip", "source_url": "https://example.com/playlist/1", "tracks": [{"title": "S", "artist": "A"}]}',
        "this is not json at all",
        "",
        '{"half": "a file',
        '{"tracks": [ broken',
    ],
)
def test_other_json_files_are_passed_over(tmp_path, content):
    (tmp_path / "other.json").write_text(content, encoding="utf-8")
    export(tmp_path, "daily_mix_1.json", exported=ago(minutes=5))
    found = find(tmp_path)
    assert names(found.selected) == ["daily_mix_1.json"]
    assert found.unrelated == 1
    assert found.broken == [] and found.older_broken == []


def test_files_that_are_not_json_by_name_are_not_even_opened(tmp_path):
    good = export(tmp_path, "daily_mix_1.json", exported=ago(minutes=5))
    for other in ("daily_mix_1.txt", "daily_mix_1.json.crdownload", "daily_mix_1", "notes.md"):
        (tmp_path / other).write_bytes(good.read_bytes())
    found = find(tmp_path)
    assert names(found.selected) == ["daily_mix_1.json"]
    assert found.unrelated == 0


def test_hidden_files_and_folders_named_like_exports_are_passed_over(tmp_path):
    export(tmp_path, ".daily_mix_1.json", exported=ago(minutes=5))
    (tmp_path / "daily_mix_2.json").mkdir()
    found = find(tmp_path)
    assert found.selected == []
    assert found.unrelated == 1  # the folder; the hidden file is not looked at


def test_bytes_that_are_not_text_are_passed_over(tmp_path):
    (tmp_path / "blob.json").write_bytes(bytes(range(256)) * 20)
    assert find(tmp_path).unrelated == 1


def test_a_very_large_file_is_not_read(tmp_path, monkeypatch):
    path = export(tmp_path, "huge.json", exported=ago(minutes=5))
    monkeypatch.setattr(downloads, "MAX_EXPORT_BYTES", path.stat().st_size - 1)
    found = find(tmp_path)
    assert found.selected == [] and found.unrelated == 1


def test_a_file_that_cannot_be_read_is_passed_over(tmp_path):
    path = export(tmp_path, "locked.json", exported=ago(minutes=5))
    path.chmod(0)
    try:
        found = find(tmp_path)
    finally:
        path.chmod(0o644)
    assert found.selected == [] and found.unrelated == 1


# --- files that are ours, but damaged -----------------------------------------------------------


def cut_off(folder: Path, filename: str, name: str = "Daily Mix 1", modified: datetime | None = None, keep: int = 160) -> Path:
    """An export that stops part-way through, as a download that did not finish would."""
    whole = json.dumps({"playlist_name": name, "source_url": SOURCE, "exported_at": stamp(NOW), "tracks": [TRACK] * 5}, indent=2)
    path = folder / filename
    path.write_text(whole[:keep], encoding="utf-8")
    touch(path, modified or ago(minutes=5))
    return path


def test_a_cut_off_export_is_recognised_and_reported(tmp_path):
    path = cut_off(tmp_path, "daily_mix_1.json")
    found = find(tmp_path)
    assert found.selected == [] and found.unrelated == 0
    ((item, good),) = found.broken
    assert item.path == path
    assert item.playlist_name == "Daily Mix 1"
    assert item.destination == "Spotify Daily Mix 1"
    assert "not valid JSON" in item.reason
    assert good is None


@pytest.mark.parametrize(
    ("tracks", "reason"),
    [
        ([], "the tracks list is empty"),
        ("fifty", "expected an object like"),
        ([{"title": "No artist"}, {"artist": "No title"}], "none of its entries can be used"),
    ],
)
def test_an_export_the_importer_cannot_use_is_reported(tmp_path, tracks, reason):
    export(tmp_path, "daily_mix_1.json", exported=ago(minutes=5), tracks=tracks)
    ((item, _),) = find(tmp_path).broken
    assert reason in item.reason
    assert str(tmp_path) not in item.reason, "the folder is not repeated in the reason"
    assert item.destination == "Spotify Daily Mix 1"


@pytest.mark.parametrize("name", ["", "   ", None, 7])
def test_an_export_that_does_not_say_which_playlist_it_is_is_damaged(tmp_path, name):
    # The importer alone would name it after the file, and a playlist would be made
    # for "daily_mix_1 (1)".
    export(tmp_path, "daily_mix_1 (1).json", name, exported=ago(minutes=5))
    found = find(tmp_path)
    assert found.selected == []
    ((item, _),) = found.broken
    assert item.reason == "it does not say which playlist it is"
    assert item.destination == ""


def test_a_damaged_export_that_is_the_newest_keeps_the_older_one_from_being_used(tmp_path):
    good = export(tmp_path, "daily_mix_1.json", exported=ago(hours=5))
    bad = cut_off(tmp_path, "daily_mix_1 (1).json", modified=ago(minutes=10))
    other = export(tmp_path, "daily_mix_2.json", "Daily Mix 2", exported=ago(minutes=9))
    found = find(tmp_path)
    assert found.paths == [other], "Daily Mix 1 is left out altogether; Daily Mix 2 is not affected"
    ((item, held_back),) = found.broken
    assert item.path == bad
    assert held_back.path == good
    assert found.older == [] and found.stale == []


def test_a_damaged_export_older_than_a_good_one_does_not_matter(tmp_path):
    cut_off(tmp_path, "daily_mix_1.json", modified=ago(hours=5))
    good = export(tmp_path, "daily_mix_1 (1).json", exported=ago(minutes=10))
    found = find(tmp_path)
    assert found.paths == [good]
    assert found.broken == []
    assert names(i for i in found.older_broken) == ["daily_mix_1.json"]


def test_a_damaged_export_too_old_to_matter_is_not_a_problem(tmp_path):
    cut_off(tmp_path, "daily_mix_1.json", modified=ago(days=5))
    found = find(tmp_path)
    assert found.broken == []
    assert names(found.older_broken) == ["daily_mix_1.json"]


def test_a_damaged_file_that_cannot_say_its_playlist_is_placed_by_its_name(tmp_path):
    good = export(tmp_path, "daily_mix_1.json", exported=ago(hours=5))
    # Cut off inside the line that would have carried the name.
    bad = cut_off(tmp_path, "daily_mix_1 (3).json", modified=ago(minutes=10), keep=24)
    bad.write_text(bad.read_text() + '\n  "tracks": [', encoding="utf-8")
    touch(bad, ago(minutes=10))
    found = find(tmp_path)
    assert found.selected == []
    ((item, held_back),) = found.broken
    assert item.playlist_name == "" and held_back.path == good


def test_a_damaged_file_for_an_unknown_playlist_is_reported_and_blocks_nothing(tmp_path):
    good = export(tmp_path, "daily_mix_1.json", exported=ago(minutes=30))
    export(tmp_path, "mystery.json", "", exported=ago(minutes=5))
    found = find(tmp_path)
    assert found.paths == [good]
    ((item, held_back),) = found.broken
    assert item.path.name == "mystery.json" and held_back is None


def test_a_damaged_newest_export_is_reported_even_when_the_good_one_is_too_old(tmp_path):
    export(tmp_path, "daily_mix_1.json", exported=ago(days=4))
    cut_off(tmp_path, "daily_mix_1 (1).json", modified=ago(minutes=10))
    found = find(tmp_path)
    assert found.selected == [] and found.stale == []
    assert len(found.broken) == 1


# --- nothing is changed -----------------------------------------------------------------------


def test_looking_changes_nothing_in_the_folder(tmp_path):
    export(tmp_path, "daily_mix_1.json", exported=ago(hours=3))
    export(tmp_path, "daily_mix_1 (1).json", exported=ago(minutes=5))
    export(tmp_path, "old.json", "Daily Mix 9", exported=ago(days=30))
    cut_off(tmp_path, "daily_mix_2.json", "Daily Mix 2")
    (tmp_path / "package.json").write_text('{"name": "x"}')

    def snapshot():
        return {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in sorted(tmp_path.iterdir())}

    before = snapshot()
    find(tmp_path)
    assert snapshot() == before


# --- the file name the extension gives a playlist -------------------------------------------------


@pytest.mark.parametrize(
    ("name", "slug"),
    [
        ("Daily Mix 1", "daily_mix_1"),
        ("  Daily   Mix 3  ", "daily_mix_3"),
        ("Today’s Top Hits", "todays_top_hits"),
        ("Rock & Roll: The 70's!", "rock_roll_the_70s"),
        ("K-Pop ON! (온)", "k_pop_on_온"),
        ("Café Français", "café_français"),
        ("हिंदी मिक्स", "हिंदी_मिक्स"),
        ("!!!", ""),
    ],
)
def test_export_slug_matches_the_extensions_file_names(name, slug):
    # The same cases as exportFilename in tests/extension/export_format.test.js.
    assert export_slug(name) == slug
