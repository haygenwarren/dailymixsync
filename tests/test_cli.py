import sqlite3

import pytest

from daily_mix_sync.cli import main


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    """Run each CLI test from an empty directory so nothing touches ./data."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def match_args(sample_playlist_path, mock_catalog_path, *extra):
    return ["match", str(sample_playlist_path), "--mock-catalog", str(mock_catalog_path), *extra]


def test_match_reports_the_summary(workdir, capsys, sample_playlist_path, mock_catalog_path):
    assert main(match_args(sample_playlist_path, mock_catalog_path)) == 0
    out = capsys.readouterr().out
    assert out.startswith(
        "Daily Mix 1 (sample)\n"
        "--------------------\n"
        "Tracks found:       11\n"
        "Cached matches:      0\n"
        "New matches:         8\n"
        "Needs review:        1\n"
        "Failed:              2\n"
    )
    assert "Needs review:\n  Let It Go" in out
    assert "qualifier mismatch: from frozen soundtrack" in out
    assert "version mismatch: acoustic" in out
    assert "no search results" in out
    assert "Matched:" not in out


def test_second_match_run_is_served_from_the_cache(
    workdir, capsys, sample_playlist_path, mock_catalog_path
):
    main(match_args(sample_playlist_path, mock_catalog_path))
    capsys.readouterr()
    assert main(match_args(sample_playlist_path, mock_catalog_path, "--details")) == 0
    out = capsys.readouterr().out
    assert "Cached matches:      8\nNew matches:         0\n" in out
    assert "cached      id mock-1003  (auto, score 100.0, first matched " in out


def test_mock_runs_use_their_own_database(workdir, sample_playlist_path, mock_catalog_path):
    main(match_args(sample_playlist_path, mock_catalog_path))
    assert (workdir / "data" / "mock_mappings.sqlite3").exists()
    assert not (workdir / "data" / "mappings.sqlite3").exists()


def test_db_option_overrides_the_database_location(
    workdir, sample_playlist_path, mock_catalog_path
):
    main(match_args(sample_playlist_path, mock_catalog_path, "--db", "custom.sqlite3"))
    with sqlite3.connect(workdir / "custom.sqlite3") as conn:
        assert conn.execute("SELECT COUNT(*) FROM mappings").fetchone() == (8,)
    assert not (workdir / "data").exists()


def test_details_lists_every_match(workdir, capsys, sample_playlist_path, mock_catalog_path):
    main(match_args(sample_playlist_path, mock_catalog_path, "--details"))
    out = capsys.readouterr().out
    assert "Matched:" in out
    assert out.count("      new ") == 8
    assert "(id mock-5002)" in out


def test_verbose_logs_candidate_scores(workdir, capsys, sample_playlist_path, mock_catalog_path):
    main(match_args(sample_playlist_path, mock_catalog_path, "-vv"))
    err = capsys.readouterr().err
    assert "search 'hotel california eagles' returned 2 candidate(s)" in err
    assert "version mismatch: live" in err


def test_match_without_a_catalog_explains_what_is_missing(workdir, capsys, sample_playlist_path):
    assert main(["match", str(sample_playlist_path)]) == 1
    assert "not implemented yet" in capsys.readouterr().err
    assert not (workdir / "data").exists()


def test_matching_settings_come_from_the_config_file(
    workdir, capsys, sample_playlist_path, mock_catalog_path
):
    (workdir / "strict.json").write_text('{"matching": {"auto_accept_threshold": 99}}')
    main(match_args(sample_playlist_path, mock_catalog_path, "--config", "strict.json"))
    out = capsys.readouterr().out
    # Déjà Vu (93.5) and Hotel California (98.0) drop from "matched" to "review".
    assert "New matches:         6\nNeeds review:        3\n" in out


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (None, "error: cannot read"),
        ("{broken", "is not valid JSON"),
        ('{"tracks": []}', "tracks list is empty"),
    ],
)
def test_bad_playlist_file_is_reported_without_a_traceback(
    workdir, capsys, mock_catalog_path, content, message
):
    path = workdir / "playlist.json"
    if content is not None:
        path.write_text(content)
    assert main(["match", str(path), "--mock-catalog", str(mock_catalog_path)]) == 1
    captured = capsys.readouterr()
    assert message in captured.err
    assert captured.out == ""


def test_bad_config_and_bad_catalog_are_reported(
    workdir, capsys, sample_playlist_path, mock_catalog_path
):
    (workdir / "bad.json").write_text('{"matching": {"review_threshold": 99}}')
    assert main(match_args(sample_playlist_path, mock_catalog_path, "--config", "bad.json")) == 1
    assert "review_threshold must not exceed" in capsys.readouterr().err

    assert main(["match", str(sample_playlist_path), "--mock-catalog", "missing.json"]) == 1
    assert "cannot read mock catalog" in capsys.readouterr().err


def test_validate_lists_tracks_and_keys(workdir, capsys, sample_playlist_path):
    assert main(["validate", str(sample_playlist_path)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Daily Mix 1 (sample): 11 usable track(s)\n")
    assert "   1. Mr. Brightside — The Killers [Hot Fuss] 3:42" in out
    assert "key: spotify:track:SAMPLE0000000000000006" in out  # derived from the URL
    assert "key: meta:rage against the machine|killing in the name|" in out
    assert "  11. Dreams — Fleetwood Mac -:--" in out


def test_validate_reports_unusable_entries(workdir, capsys):
    path = workdir / "playlist.json"
    path.write_text(
        '{"playlist_name": "Mix", "tracks": ['
        '{"title": "A", "artist": "X", "spotify_track_id": "1"},'
        '{"title": "A", "artist": "X", "spotify_track_id": "1"},'
        '{"title": "No Artist"}]}'
    )
    assert main(["validate", str(path)]) == 0
    out = capsys.readouterr().out
    assert "Mix: 1 usable track(s)" in out
    assert "Duplicates dropped: 1" in out
    assert "Unusable: track #3 ('No Artist'): missing artist" in out
