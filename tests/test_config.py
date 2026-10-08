import json
from pathlib import Path

import pytest

from daily_mix_sync.config import ConfigError, Settings, load_settings
from daily_mix_sync.matcher import DEFAULT_FLAG_PENALTIES

EXAMPLE = Path(__file__).resolve().parent.parent / "config.example.json"


def write(tmp_path, payload):
    path = tmp_path / "config.json"
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
    return path


def test_defaults_when_there_is_no_config_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert load_settings() == Settings()


def test_config_json_in_the_working_directory_is_picked_up(tmp_path, monkeypatch):
    write(tmp_path, {"search_limit": 25})
    monkeypatch.chdir(tmp_path)
    assert load_settings().search_limit == 25


def test_example_config_spells_out_the_defaults():
    assert load_settings(EXAMPLE) == Settings()


def test_the_search_depths_can_be_set(tmp_path):
    settings = load_settings(write(tmp_path, {"search_limit": 40, "title_search_limit": 12}))
    assert (settings.search_limit, settings.title_search_limit) == (40, 12)


def test_the_title_search_can_be_turned_off_in_the_config(tmp_path):
    assert load_settings(write(tmp_path, {"title_search_limit": 0})).title_search_limit == 0


def test_the_default_depths_are_the_measured_ones():
    # Chosen from a measurement over a real library; see the comment in config.py.
    assert (Settings().search_limit, Settings().title_search_limit) == (60, 25)


def test_overrides(tmp_path):
    settings = load_settings(
        write(
            tmp_path,
            {
                "database_path": "elsewhere/cache.sqlite3",
                "matching": {"auto_accept_threshold": 95, "flag_penalties": {"live": 50}},
            },
        )
    )
    assert settings.database_path == Path("elsewhere/cache.sqlite3")
    assert settings.managed_playlist_prefix == "Spotify Daily Mix"
    assert settings.matching.auto_accept_threshold == 95
    assert settings.matching.review_threshold == 75
    assert settings.matching.flag_penalties == {**DEFAULT_FLAG_PENALTIES, "live": 50}


def test_managed_playlist_prefix_can_be_changed(tmp_path):
    settings = load_settings(write(tmp_path, {"managed_playlist_prefix": "Mirror"}))
    assert settings.managed_playlist_prefix == "Mirror"


def test_catalog_wait_can_be_changed(tmp_path):
    assert load_settings(write(tmp_path, {"catalog_wait_s": 60})).catalog_wait_s == 60
    assert Settings().catalog_wait_s == 30


def test_missing_explicit_config_file(tmp_path):
    with pytest.raises(ConfigError, match="cannot read config"):
        load_settings(tmp_path / "nope.json")


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ("{oops", "not valid JSON"),
        ([1, 2], "must be a JSON object"),
        ({"databse_path": "x"}, r"unknown config .* key\(s\) \['databse_path'\]"),
        ({"matching": {"auto_accept": 90}}, r"unknown 'matching' key\(s\) \['auto_accept'\]"),
        ({"matching": []}, "'matching' must be an object"),
        ({"matching": {"review_threshold": 99}}, "review_threshold must not exceed"),
        ({"matching": {"auto_accept_threshold": "high"}}, "config"),
        ({"matching": {"flag_penalties": {"liev": 1}}}, "unknown flag_penalties keys"),
        ({"search_limit": 0}, "search_limit must be"),
        ({"search_limit": "ten"}, "search_limit must be"),
        ({"title_search_limit": -1}, "title_search_limit must be"),
        ({"title_search_limit": "deep"}, "title_search_limit must be"),
        ({"title_search_limit": True}, "title_search_limit must be"),
        ({"title_search_limit": 2.5}, "title_search_limit must be"),
        ({"managed_playlist_prefix": ""}, "managed_playlist_prefix must be"),
        ({"managed_playlist_prefix": " Spotify Daily Mix"}, "managed_playlist_prefix must be"),
        ({"managed_playlist_prefix": 7}, "managed_playlist_prefix must be"),
        ({"catalog_wait_s": 0}, "catalog_wait_s must be"),
        ({"catalog_wait_s": "long"}, "catalog_wait_s must be"),
    ],
)
def test_invalid_config_is_explained(tmp_path, payload, message):
    with pytest.raises(ConfigError, match=message):
        load_settings(write(tmp_path, payload))
