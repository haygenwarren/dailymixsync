"""What --help says: short, and true to what the commands do."""

from __future__ import annotations

import re

import pytest

from daily_mix_sync import cli


def help_text(capsys, *command: str) -> str:
    with pytest.raises(SystemExit) as stopped:
        cli.main([*command, "--help"])
    assert stopped.value.code == 0
    return capsys.readouterr().out


def as_one_line(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def test_the_top_level_help_says_what_the_tool_does_and_which_command_is_the_everyday_one(capsys):
    text = help_text(capsys)
    assert text.startswith("usage: daily_mix_sync [-h] COMMAND ...\n")
    flat = as_one_line(text)
    assert "using only songs that are already in your Music library" in flat
    assert "sync-downloads everyday use: sync the recent exports found in your Downloads folder" in flat
    assert "Everyday use: export your Daily Mixes with the Chrome extension, then run 'sync-downloads --dry-run' and 'sync-downloads'." in flat


def test_the_top_level_help_lists_every_command_once_with_the_everyday_ones_first(capsys):
    text = help_text(capsys)
    listed = re.findall(r"^    ([a-z][a-z-]+)\b", text, flags=re.M)
    assert listed[:5] == ["sync-downloads", "sync", "validate", "match", "review"]
    assert len(listed) == len(set(listed))
    assert "{" not in text, "no wall of command names in braces"
    experimental = [name for name in listed if name.startswith("experimental-")]
    assert listed[-len(experimental):] == experimental, "the experimental commands come last"


def command_list(text: str) -> dict[str, str]:
    """The commands in the top-level help, each with its description on one line."""
    entries: dict[str, str] = {}
    name = None
    for line in text.split("commands:")[1].split("\n\n")[0].splitlines():
        started = re.match(r"^    ([a-z][a-z-]+)(?:\s+(.*))?$", line)
        if started:
            name = started.group(1)
            entries[name] = started.group(2) or ""
        elif name and line.startswith("      "):
            entries[name] = f"{entries[name]} {line.strip()}".strip()
    return entries


def test_nothing_about_the_catalog_is_offered_outside_the_experimental_commands(capsys):
    entries = command_list(help_text(capsys))
    assert len(entries) == 14
    for name, description in entries.items():
        if name.startswith("experimental-"):
            assert description.startswith("EXPERIMENTAL: "), name
        else:
            assert "catalog" not in description.lower(), name
            assert "ADD" not in description and "EXPERIMENTAL" not in description, name


def test_sync_downloads_help_says_the_four_things_that_matter(capsys):
    flat = as_one_line(help_text(capsys, "sync-downloads"))
    assert "Find the recent Spotify exports in your Downloads folder and sync them." in flat
    assert "The newest export of each playlist is used" in flat
    assert "Only songs already in your Music library are written, and nothing is added to it." in flat
    assert "they are never moved or deleted" in flat


def test_sync_downloads_help_lists_its_options_and_not_into(capsys):
    text = help_text(capsys, "sync-downloads")
    for option in ("--downloads-dir PATH", "--max-age HOURS", "--list", "--dry-run", "--yes", "--no-review",
                   "--db FILE", "--details", "--config FILE", "-v, --verbose"):
        assert option in text, option
    assert "--into" not in text


def test_sync_downloads_help_stays_short(capsys):
    text = help_text(capsys, "sync-downloads")
    description = text.split("\n\n")[1]
    assert len(description.splitlines()) <= 6, "the README carries the detail"
    assert len(text.splitlines()) <= 45


def test_sync_help_describes_naming_files_and_points_to_the_everyday_command(capsys):
    text = help_text(capsys, "sync")
    flat = as_one_line(text)
    assert "Sync the exports you name." in flat
    assert "songs that are already in your Music library" in flat
    assert "nothing is added to the library" in flat
    assert "For everyday use, see sync-downloads." in flat
    assert "EXPORT [EXPORT ...]" in text
    assert "--into NAME" in text


@pytest.mark.parametrize(
    "command",
    ["validate", "match", "review", "sync", "sync-downloads", "music-test", "music-playlists",
     "music-find", "music-add-test", "music-clear-test"],
)
def test_every_supported_command_has_help_that_says_nothing_of_moving_files_or_the_catalog(capsys, command):
    text = help_text(capsys, command)
    assert text.startswith(f"usage: daily_mix_sync {command} ")
    lowered = text.lower()
    assert "mv " not in lowered
    if command not in ("match", "review"):  # those two can search a mock catalog, a test fixture
        assert "catalog" not in lowered
    assert "accessibility" not in lowered
