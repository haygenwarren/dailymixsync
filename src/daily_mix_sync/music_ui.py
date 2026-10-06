"""Operate the Music window itself, through macOS Accessibility (System Events).

AppleScript cannot search the Apple Music catalog or add a song that is not in the
library, so those songs will have to be added the way a person would: search field,
results, "Add to Playlist". Everything that depends on how the Music window is laid
out belongs in this module and nowhere else.

So far it holds only the permission check. The window's element hierarchy has to be
inspected on the installed app before anything is built on it, and macOS does not
allow that inspection until Accessibility permission has been granted.
"""

from __future__ import annotations

from .music_app import ScriptRunner, run_osascript

# True only when the program asking is trusted for Accessibility.
_UI_ELEMENTS_ENABLED = 'tell application "System Events" to return UI elements enabled'


def accessibility_allowed(run: ScriptRunner = run_osascript) -> bool:
    """Whether macOS will let this program read and operate the Music window."""
    return run(_UI_ELEMENTS_ENABLED, []) == "true"
