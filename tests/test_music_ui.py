"""Unit tests for the Music-window module. Nothing here touches the real GUI."""

import re

import pytest

from daily_mix_sync import music_ui
from daily_mix_sync.music_app import (
    AccessibilityPermissionError,
    MusicAppError,
    MusicPermissionError,
    error_from_osascript,
)
from daily_mix_sync.music_ui import (
    CatalogUIResult,
    MusicCatalogUI,
    MusicUIError,
    MusicUILayoutError,
)

US, RS = "\x1f", "\x1e"
ROW_ID = "Music.shelfItem.TrackLockup[id=track-section-song-{},parentId=track-section-song]"


def row(catalog_id, title, artist):
    return US.join([ROW_ID.format(catalog_id), title, artist])


class Runner:
    """Stands in for osascript: answers each script from a table and records the calls."""

    def __init__(self, replies=None):
        self.replies = dict(replies or {})
        self.calls = []

    def __call__(self, script, args):
        self.calls.append((script, list(args)))
        reply = self.replies.get(script, "")
        if isinstance(reply, Exception):
            raise reply
        return reply

    def scripts(self):
        return [script for script, _ in self.calls]


def script_error(message, code):
    return error_from_osascript(f"12:34: execution error: {message} ({code})")


def code_of(script):
    """The script without its comments."""
    return "\n".join(line for line in script.splitlines() if not line.strip().startswith("--"))


def body_of(script):
    """The part of a script after the shared handlers."""
    return code_of(script.replace(music_ui._HELPERS, ""))


# --- permission -------------------------------------------------------------------


@pytest.mark.parametrize(("reply", "allowed"), [("true", True), ("false", False)])
def test_accessibility_allowed_reads_system_events_answer(reply, allowed):
    runner = Runner({music_ui._UI_ELEMENTS_ENABLED: reply})
    assert music_ui.accessibility_allowed(runner) is allowed
    assert MusicCatalogUI(run=runner).accessibility_allowed() is allowed
    assert runner.calls[0] == ('tell application "System Events" to return UI elements enabled', [])


def test_accessibility_check_passes_on_a_refusal_to_talk_to_system_events():
    denied = Runner({music_ui._UI_ELEMENTS_ENABLED: script_error(
        "Not authorized to send Apple events to System Events.", -1743)})
    with pytest.raises(MusicPermissionError):
        music_ui.accessibility_allowed(denied)


def test_require_accessibility_explains_where_to_grant_it():
    ui = MusicCatalogUI(run=Runner({music_ui._UI_ELEMENTS_ENABLED: "false"}))
    with pytest.raises(AccessibilityPermissionError) as caught:
        ui.require_accessibility()
    message = str(caught.value)
    assert message.startswith("Searching the Apple Music catalog needs macOS Accessibility permission.")
    assert "System Settings → Privacy & Security → Accessibility" in message
    MusicCatalogUI(run=Runner({music_ui._UI_ELEMENTS_ENABLED: "true"})).require_accessibility()


# --- searching --------------------------------------------------------------------


def test_search_returns_the_song_rows_in_page_order():
    reply = RS.join([
        row("1440891171", "Mr. Brightside", "The Killers"),
        row("1392833532", "Mr. Brightside (Live from The Royal Albert Hall / 2009)", "The Killers"),
        row("6782260222", "Mr. Brightside ￼", "Irish  Made"),
    ])
    runner = Runner({music_ui._SEARCH: reply})
    results = MusicCatalogUI(run=runner).search_catalog("mr brightside killers")
    assert results == [
        CatalogUIResult("Mr. Brightside", "The Killers", 1, ROW_ID.format("1440891171")),
        CatalogUIResult("Mr. Brightside (Live from The Royal Albert Hall / 2009)", "The Killers", 2, ROW_ID.format("1392833532")),
        # The inline badge the window shows beside a title is not part of the title.
        CatalogUIResult("Mr. Brightside", "Irish Made", 3, ROW_ID.format("6782260222")),
    ]
    assert [r.catalog_id for r in results] == ["1440891171", "1392833532", "6782260222"]


def test_search_sends_the_query_and_the_patience_as_arguments():
    runner = Runner()
    MusicCatalogUI(run=runner, results_timeout_s=9).search_catalog("  nutshell   alice in chains ")
    assert runner.calls == [(music_ui._SEARCH, ["nutshell alice in chains", "9"])]
    assert "nutshell" not in music_ui._SEARCH  # the query never becomes script text


@pytest.mark.parametrize("term", ["", "   "])
def test_an_empty_search_is_not_sent_to_the_window(term):
    runner = Runner()
    assert MusicCatalogUI(run=runner).search_catalog(term) == []
    assert runner.calls == []


def test_a_search_with_no_songs_is_an_empty_list():
    assert MusicCatalogUI(run=Runner({music_ui._SEARCH: ""})).search_catalog("x") == []


def test_a_row_without_a_catalog_id_still_works_as_a_handle():
    result = CatalogUIResult("Song", "Artist", 1, "Music.shelfItem.SomethingNew[id=abc]")
    assert result.catalog_id is None and result.element_id == "Music.shelfItem.SomethingNew[id=abc]"


def test_a_malformed_row_is_a_layout_error_not_a_guess():
    with pytest.raises(MusicUILayoutError, match="Unexpected song row.*experimental-ui-inspect"):
        MusicCatalogUI(run=Runner({music_ui._SEARCH: "only-one-field"})).search_catalog("x")


# --- adding -----------------------------------------------------------------------

RESULT = CatalogUIResult("Mr. Brightside", "The Killers", 1, ROW_ID.format("1440891171"))


@pytest.mark.parametrize(("reply", "added"), [("added", True), ("present", False)])
def test_add_to_library_reports_whether_it_did_anything(reply, added):
    runner = Runner({music_ui._MENU: reply})
    assert MusicCatalogUI(run=runner).add_to_library(RESULT) is added
    assert runner.calls == [(music_ui._MENU, [RESULT.element_id, "add"])]


@pytest.mark.parametrize(("reply", "present"), [("present", True), ("absent", False)])
def test_in_library_only_reads_the_menu(reply, present):
    runner = Runner({music_ui._MENU: reply})
    assert MusicCatalogUI(run=runner).in_library(RESULT) is present
    assert runner.calls == [(music_ui._MENU, [RESULT.element_id, "check"])]


# --- failures ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "code", "kind", "stops_the_step"),
    [
        ("The search field was not found in Music's toolbar.", 9102, MusicUILayoutError, True),
        ("Music's window did not come to the front within 5 seconds.", 9101, MusicUILayoutError, True),
        ("Music lost the keyboard before the search could be typed; nothing was typed.", 9103, MusicUIError, False),
        ('Apple Music showed no results for "x" within 15 seconds.', 9104, MusicUIError, False),
        ("The chosen song is no longer among the results on screen.", 9105, MusicUIError, False),
        ("The song result's menu offers neither Add to Library nor Delete from Library (it offers: Play Next).", 9106, MusicUIError, False),
        # System Events could not find something the script addressed directly.
        ('System Events got an error: Can’t get scroll bar 1 of scroll area 2 of splitter group 1 of window "Music" of application process "Music". Invalid index.', -1719, MusicUILayoutError, True),
        ('System Events got an error: Can’t get window "Music" of application process "Music".', -1728, MusicUILayoutError, True),
    ],
)
def test_window_failures_are_sorted_into_kinds(message, code, kind, stops_the_step):
    ui = MusicCatalogUI(run=Runner({music_ui._SEARCH: script_error(message, code)}))
    with pytest.raises(MusicUIError) as caught:
        ui.search_catalog("x")
    assert type(caught.value) is kind
    assert isinstance(caught.value, MusicUILayoutError) is stops_the_step
    text = str(caught.value)
    assert "AppleScript error" not in text
    assert ("python -m daily_mix_sync experimental-ui-inspect" in text) is stops_the_step
    assert message.split(".")[0][:40] in text


def test_a_lost_accessibility_permission_is_reported_as_that():
    denied = script_error("System Events got an error: osascript is not allowed assistive access.", -25211)
    with pytest.raises(AccessibilityPermissionError):
        MusicCatalogUI(run=Runner({music_ui._SEARCH: denied})).search_catalog("x")


def test_other_failures_pass_through_unchanged():
    timeout = MusicAppError("Music did not answer within 120s.")
    with pytest.raises(MusicAppError) as caught:
        MusicCatalogUI(run=Runner({music_ui._MENU: timeout})).add_to_library(RESULT)
    assert caught.value is timeout


# --- taking the screen and giving it back -------------------------------------------


def test_session_puts_the_previous_app_back_in_front():
    runner = Runner({music_ui._FRONTMOST_APP: "Terminal"})
    ui = MusicCatalogUI(run=runner)
    with ui.session():
        ui.search_catalog("x")
    assert runner.calls == [
        (music_ui._FRONTMOST_APP, []), (music_ui._SEARCH, ["x", "15"]), (music_ui._ACTIVATE, ["Terminal"]),
    ]


def test_session_gives_the_screen_back_even_when_the_work_fails():
    runner = Runner({music_ui._FRONTMOST_APP: "Code", music_ui._SEARCH: script_error("boom", 9104)})
    ui = MusicCatalogUI(run=runner)
    with pytest.raises(MusicUIError):
        with ui.session():
            ui.search_catalog("x")
    assert runner.calls[-1] == (music_ui._ACTIVATE, ["Code"])


def test_session_does_not_reactivate_music_itself():
    runner = Runner({music_ui._FRONTMOST_APP: "Music"})
    with MusicCatalogUI(run=runner).session():
        pass
    assert music_ui._ACTIVATE not in runner.scripts()


def test_session_survives_not_knowing_what_was_in_front():
    runner = Runner({music_ui._FRONTMOST_APP: MusicAppError("System Events did not answer")})
    with MusicCatalogUI(run=runner).session():
        pass
    assert music_ui._ACTIVATE not in runner.scripts()


def test_hand_back_in_the_middle_of_a_session_for_a_question_in_the_terminal():
    runner = Runner({music_ui._FRONTMOST_APP: "iTerm2"})
    ui = MusicCatalogUI(run=runner)
    with ui.session():
        ui.hand_back()
        assert runner.calls[-1] == (music_ui._ACTIVATE, ["iTerm2"])
    assert runner.scripts().count(music_ui._ACTIVATE) == 2
    ui.hand_back()  # outside a session there is nothing to give back
    assert runner.scripts().count(music_ui._ACTIVATE) == 2


# --- inspecting -------------------------------------------------------------------


def test_inspect_turns_the_script_report_into_rows():
    report = "\n".join([
        US.join(["ok", "main window", "Music, 1 window(s) in all"]),
        US.join(["missing", "search field", "The search field was not found in Music's toolbar."]),
        US.join(["info", "Songs section", "not present"]),
        US.join(["rows", "song rows", row("1", "Dreams", "Fleetwood Mac") + RS + row("2", "Dreams (Live)", "Fleetwood Mac")]),
    ])
    assert MusicCatalogUI(run=Runner({music_ui._INSPECT: report})).inspect() == [
        ("ok", "main window", "Music, 1 window(s) in all"),
        ("missing", "search field", "The search field was not found in Music's toolbar."),
        ("info", "Songs section", "not present"),
        ("ok", "song rows", "Dreams — Fleetwood Mac; Dreams (Live) — Fleetwood Mac"),
    ]


def test_dump_names_the_part():
    runner = Runner({music_ui._DUMP: "group 1 of toolbar 1"})
    assert MusicCatalogUI(run=runner).dump("toolbar") == "group 1 of toolbar 1"
    assert runner.calls == [(music_ui._DUMP, ["toolbar"])]


# --- what the scripts are allowed to do -----------------------------------------------

ALL_SCRIPTS = {
    "search": music_ui._SEARCH, "menu": music_ui._MENU,
    "inspect": music_ui._INSPECT, "dump": music_ui._DUMP,
}


def test_add_to_library_is_the_only_menu_item_ever_chosen():
    menu = body_of(music_ui._MENU)
    presses = [line.strip() for line in menu.splitlines() if "AXPress" in line]
    assert presses == [
        'perform action "AXPress" of moreButton',  # opens the menu
        'perform action "AXPress" of addItem',  # the one item it may choose
    ]
    assert 'if itemName is "Add to Library" then set addItem to menu item k of theMenu' in menu
    assert "click" not in menu
    # "Delete from Library" is only ever looked at, to tell that a song is already there.
    assert [line.strip() for line in menu.splitlines() if "Delete from Library" in line] == [
        'if itemNames contains "Delete from Library" then return "present"',
        'error "The song result\'s menu offers neither Add to Library nor Delete from Library '
        '(it offers: " & my joined(itemNames, ", ") & ")." number 9106',
    ]


def test_the_menu_is_closed_without_a_keystroke_when_nothing_is_chosen():
    menu = body_of(music_ui._MENU)
    assert menu.count('perform action "AXCancel" of theMenu') == 2
    assert "keystroke" not in menu and "key code" not in menu


def test_check_mode_never_reaches_the_add():
    menu = body_of(music_ui._MENU)
    guard = menu.index('if addItem is not missing value and mode is "add" then')
    assert guard < menu.index('perform action "AXPress" of addItem')
    assert menu.count('perform action "AXPress" of addItem') == 1


def test_nothing_is_typed_unless_music_and_the_search_field_have_the_keyboard():
    lines = [line.strip() for line in body_of(music_ui._SEARCH).splitlines() if line.strip()]
    typed = [i for i, line in enumerate(lines) if line.startswith(("keystroke", "key code"))]
    assert [lines[i] for i in typed] == ["keystroke query", "key code 36"]
    for i in typed:
        assert lines[i - 1].startswith("if not ((focused of field) and frontmost) then error ")
    # Before Return, what the field holds is compared with the query.
    submit = lines.index("key code 36")
    assert any(line.startswith("if (value of field) is not query then error") for line in lines[:submit])


def test_no_destructive_keystrokes_are_used_to_clear_the_field():
    search = body_of(music_ui._SEARCH)
    assert 'set value of field to ""' in search
    assert "command down" not in search and "key code 51" not in search


@pytest.mark.parametrize("name", ["inspect", "dump"])
def test_the_diagnostic_scripts_type_and_press_nothing(name):
    body = body_of(ALL_SCRIPTS[name])
    assert "keystroke" not in body and "key code" not in body
    assert "AXPress" not in body and "click" not in body


@pytest.mark.parametrize("name", ALL_SCRIPTS)
def test_no_script_uses_screen_coordinates(name):
    script = code_of(ALL_SCRIPTS[name])
    assert not re.search(r"\bclick at\b|\bposition of\b|\bAXPosition\b|\bmouse\b", script)


@pytest.mark.parametrize("name", ALL_SCRIPTS)
def test_scripts_do_not_reuse_names_that_system_events_owns(name):
    # Inside `tell application "System Events"`, "rows" means table rows; assigning
    # to it fails with "Can't set last insertion point of every row".
    script = code_of(ALL_SCRIPTS[name])
    assert not re.search(r"\bset (end of )?rows\b", script)
    assert not re.search(r"\b(set|on \w+\(.*)\bpath\b", script)


@pytest.mark.parametrize("name", ["search", "menu", "inspect", "dump"])
def test_every_script_starts_by_bringing_music_to_the_front(name):
    body = body_of(ALL_SCRIPTS[name])
    run = body[body.index("on run argv"):]
    first_use = min(i for i in (run.find("my musicWindow()"),) if i >= 0)
    assert "System Events" not in run[:first_use]


def test_the_search_checks_the_layout_before_it_types():
    run = body_of(music_ui._SEARCH)
    run = run[run.index("on run argv"):run.index("end run")]
    order = [run.index(step) for step in (
        "my musicWindow()", "my showSearchPage(win)", "my chooseAppleMusicScope(win)",
        "my searchField(win)", "my contentArea(win)", "my typeQuery(field, query)",
    )]
    assert order == sorted(order)
