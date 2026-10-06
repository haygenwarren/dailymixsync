"""Settings: built-in defaults, optionally overridden by a JSON config file.

See config.example.json for every available key. Credentials do not belong in
this file; they will come from environment variables or ignored key files.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path

from .matcher import MatchConfig

DEFAULT_CONFIG_PATH = Path("config.json")


class ConfigError(Exception):
    """The config file is unreadable or contains invalid settings."""


@dataclass(frozen=True)
class Settings:
    database_path: Path = Path("data/mappings.sqlite3")
    search_limit: int = 10  # candidates requested per catalog search
    matching: MatchConfig = field(default_factory=MatchConfig)


def _reject_unknown(given: dict, allowed: set[str], where: str) -> None:
    unknown = sorted(set(given) - allowed)
    if unknown:
        raise ConfigError(f"unknown {where} key(s) {unknown}; allowed: {sorted(allowed)}")


def load_settings(path: Path | None = None) -> Settings:
    """Load `path`, or ./config.json when it exists, or fall back to the defaults."""
    if path is None:
        if not DEFAULT_CONFIG_PATH.exists():
            return Settings()
        path = DEFAULT_CONFIG_PATH
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"cannot read config {path}: {exc.strerror or exc}") from exc
    except ValueError as exc:  # invalid JSON or not UTF-8
        raise ConfigError(f"config {path} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"config {path} must be a JSON object")

    _reject_unknown(raw, {f.name for f in dataclasses.fields(Settings)}, f"config {path}")
    matching = raw.get("matching", {})
    if not isinstance(matching, dict):
        raise ConfigError(f"config {path}: 'matching' must be an object")
    _reject_unknown(matching, {f.name for f in dataclasses.fields(MatchConfig)}, "'matching'")

    try:
        search_limit = raw.get("search_limit", Settings.search_limit)
        if isinstance(search_limit, bool) or not isinstance(search_limit, int) or search_limit < 1:
            raise ValueError("search_limit must be a whole number >= 1")
        return Settings(
            database_path=Path(raw.get("database_path", Settings.database_path)),
            search_limit=search_limit,
            matching=MatchConfig(**matching),
        )
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"config {path}: {exc}") from exc
