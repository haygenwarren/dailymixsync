from pathlib import Path

import pytest

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def pytest_addoption(parser):
    parser.addoption(
        "--music-app",
        action="store_true",
        help="also run the tests that drive the real Music app "
        '(they change only the "Spotify Daily Mix TEST" playlist)',
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--music-app"):
        return
    skip = pytest.mark.skip(reason="drives the real Music app; run pytest with --music-app")
    for item in items:
        if "music_app" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def sample_playlist_path() -> Path:
    return SAMPLES / "daily_mix_sample.json"


@pytest.fixture
def mock_catalog_path() -> Path:
    return SAMPLES / "mock_apple_catalog.json"
