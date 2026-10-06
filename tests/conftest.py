from pathlib import Path

import pytest

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


@pytest.fixture
def sample_playlist_path() -> Path:
    return SAMPLES / "daily_mix_sample.json"


@pytest.fixture
def mock_catalog_path() -> Path:
    return SAMPLES / "mock_apple_catalog.json"
