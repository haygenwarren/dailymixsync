import subprocess
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


@pytest.fixture(autouse=True)
def no_real_commands(request, monkeypatch):
    """Unit tests must never reach the real Music app, whatever the code under test does.

    Everything said to Music goes through subprocess.run (osascript), so outside
    the opt-in live tests that call simply fails.
    """
    if "music_app" in request.keywords:
        return

    def refuse(command, *args, **kwargs):
        raise AssertionError(f"a unit test tried to run a real command: {command!r}")

    monkeypatch.setattr(subprocess, "run", refuse)


@pytest.fixture
def sample_playlist_path() -> Path:
    return SAMPLES / "daily_mix_sample.json"


@pytest.fixture
def mock_catalog_path() -> Path:
    return SAMPLES / "mock_apple_catalog.json"
