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


    parser.addoption(
        "--music-ui",
        action="store_true",
        help="also run the tests that operate the real Music window through Accessibility "
        "(they search the Apple Music catalog and change nothing)",
    )


def pytest_collection_modifyitems(config, items):
    for option, marker, what in (
        ("--music-app", "music_app", "drives the real Music app"),
        ("--music-ui", "music_ui", "operates the real Music window"),
    ):
        if config.getoption(option):
            continue
        skip = pytest.mark.skip(reason=f"{what}; run pytest with {option}")
        for item in items:
            if marker in item.keywords:
                item.add_marker(skip)


@pytest.fixture(autouse=True)
def no_real_commands(request, monkeypatch):
    """Unit tests must never reach the real Music app, whatever the code under test does.

    Everything said to Music goes through subprocess.run (osascript), so outside
    the opt-in live tests that call simply fails.
    """
    if "music_app" in request.keywords or "music_ui" in request.keywords:
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
