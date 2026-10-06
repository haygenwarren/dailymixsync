"""Unit tests for the UI-automation module. Nothing here touches the real Music app."""

import pytest

from daily_mix_sync import music_ui
from daily_mix_sync.music_app import MusicPermissionError, error_from_osascript


@pytest.mark.parametrize(("reply", "allowed"), [("true", True), ("false", False)])
def test_accessibility_allowed_reads_system_events_answer(reply, allowed):
    sent = []

    def runner(script, args):
        sent.append((script, list(args)))
        return reply

    assert music_ui.accessibility_allowed(runner) is allowed
    assert sent == [('tell application "System Events" to return UI elements enabled', [])]


def test_accessibility_check_passes_on_a_refusal_to_talk_to_system_events():
    def denied(script, args):
        raise error_from_osascript(
            "0:1: execution error: Not authorized to send Apple events to System Events. (-1743)"
        )

    with pytest.raises(MusicPermissionError):
        music_ui.accessibility_allowed(denied)
