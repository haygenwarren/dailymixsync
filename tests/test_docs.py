"""The documents, the popup and the command line have to tell the same story.

These checks are deliberately mechanical. They catch the ways documentation goes
stale without anyone deciding it should: a command that was renamed, an option that
no longer exists, an old workflow still described as the normal one.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from daily_mix_sync import cli

ROOT = Path(__file__).parent.parent
README = ROOT / "README.md"
EXTENSION_README = ROOT / "extension" / "README.md"
ARCHITECTURE = ROOT / "ARCHITECTURE.md"
POPUP = ROOT / "extension" / "popup.js"
DOCUMENTS = [README, EXTENSION_README, ARCHITECTURE]

EVERYDAY = "python -m daily_mix_sync sync-downloads"


def commands() -> dict:
    return cli._build_parser()._subparsers._group_actions[0].choices


def options_of(command: str) -> set[str]:
    return {flag for action in commands()[command]._actions for flag in action.option_strings}


def invocations(text: str) -> list[tuple[str, list[str]]]:
    """Every `python -m daily_mix_sync COMMAND ...` in a text, as (command, its --options)."""
    found = []
    for match in re.finditer(r"python -m daily_mix_sync(?![\w-])([^\n`]*)", text):
        # Trailing quotes and punctuation belong to the sentence or the source line.
        words = [word.strip("\"';,.:)") for word in match.group(1).split("#")[0].split()]
        words = [word for word in words if word]
        if not words or not re.fullmatch(r"[a-z][a-z-]*", words[0]):
            continue  # prose such as "python -m daily_mix_sync <command>"
        flags = [word.split("=")[0] for word in words[1:] if re.fullmatch(r"--[a-z][a-z-]*(=.*)?", word)]
        found.append((words[0], flags))
    return found


def section(text: str, heading: str) -> str:
    """The body of one markdown section, up to the next heading of the same or a higher level."""
    level = len(heading) - len(heading.lstrip("#"))
    start = text.index(f"\n{heading}\n")
    rest = text[start + len(heading) + 2 :]
    end = re.search(rf"^#{{1,{level}}} ", rest, flags=re.M)
    return rest[: end.start()] if end else rest


@pytest.mark.parametrize("path", [*DOCUMENTS, POPUP], ids=lambda p: p.name)
def test_every_command_shown_is_a_real_command_with_real_options(path):
    shown = invocations(path.read_text(encoding="utf-8"))
    if path is not ARCHITECTURE:
        assert shown, f"{path.name} shows no commands at all"
    for command, flags in shown:
        assert command in commands(), f"{path.name} shows the command {command!r}, which does not exist"
        for flag in flags:
            assert flag in options_of(command), f"{path.name}: {command} has no option {flag}"


def test_the_popup_names_the_everyday_command_and_only_that():
    assert invocations(POPUP.read_text(encoding="utf-8")) == [("sync-downloads", [])]
    assert f'"{EVERYDAY}"' in POPUP.read_text(encoding="utf-8")


def test_the_readme_presents_sync_downloads_as_the_normal_workflow():
    text = README.read_text(encoding="utf-8")
    everyday = section(text, "## From Spotify to Apple Music")
    by_hand = section(text, "### Working with one export by hand")
    normal = everyday.replace(by_hand, "")
    assert f"{EVERYDAY} --dry-run" in normal and f"{EVERYDAY}\n" in normal
    assert "mv " not in normal and "validate" not in normal
    # Setup leads to the same place.
    setup = section(text, "## Setup")
    assert "git clone" in setup and "source .venv/bin/activate" in setup
    assert f"{EVERYDAY} --dry-run" in setup
    assert "mv " not in setup


def test_moving_an_export_by_hand_appears_only_where_it_is_called_optional():
    for path, heading in ((README, "### Working with one export by hand"),
                          (EXTENSION_README, "### Looking at one export by hand")):
        text = path.read_text(encoding="utf-8")
        by_hand = section(text, heading)
        assert "mv ~/Downloads/" in by_hand, f"{path.name} keeps the explicit workflow"
        assert "mv ~/Downloads/" not in text.replace(by_hand, ""), f"{path.name}: only in that section"
        assert re.search(r"debugging", by_hand, flags=re.I)
        assert re.search(r"never needs this|not needed for everyday use", by_hand, flags=re.I)
    assert "mv ~/Downloads" not in ARCHITECTURE.read_text(encoding="utf-8")


def test_the_extension_readme_recommends_listing_then_a_dry_run_then_the_sync():
    use = section(EXTENSION_README.read_text(encoding="utf-8"), "## Use")
    first, second, third = (use.index(f"{EVERYDAY}{suffix}") for suffix in (" --list", " --dry-run", "    "))
    assert first < second < third
    assert use.index(f"{EVERYDAY} --list") < use.index("### Looking at one export by hand")


def test_setup_explains_the_two_signs_of_an_inactive_virtual_environment():
    setup = section(README.read_text(encoding="utf-8"), "## Setup")
    for needed in ("zsh: command not found: python", "No module named daily_mix_sync",
                   "source .venv/bin/activate", "which python", ".venv/bin/python"):
        assert needed in setup, needed


def test_no_document_promises_cleanup_or_describes_finished_work_as_unfinished():
    for path in DOCUMENTS:
        text = path.read_text(encoding="utf-8")
        for stale in ("Still to do", "will produce", "Not built", "still says to move", "still suggests"):
            assert stale not in text, f"{path.name}: {stale!r}"
    readme = README.read_text(encoding="utf-8")
    assert "never\n  moved or deleted" in readme or "never moved or deleted" in readme
    assert re.search(r"[Dd]elete\s+them\s+by\s+hand", readme), "cleanup is the user's, and said to be"


def test_the_documents_agree_on_the_age_limit_and_the_settings():
    settings = cli.load_settings.__globals__["Settings"]()
    assert settings.export_max_age_hours == 24
    readme = README.read_text(encoding="utf-8")
    assert "| `export_max_age_hours` | 24 |" in readme
    assert "| `downloads_dir` | `~/Downloads` |" in readme
    assert "last 24 hours" in readme
    assert "older than a day" in EXTENSION_README.read_text(encoding="utf-8")
