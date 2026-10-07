"""EXPERIMENTAL: operate the Music window itself, through macOS Accessibility.

Not part of the supported workflow. `sync`, `match` and `review` never import or call
this module, and need no Accessibility permission. It is reached through the
experimental-* commands alone.

AppleScript cannot search the Apple Music catalog or add a song that is not in the
library, so those two things are done the way a person would: the sidebar's Search
entry, the search field, the Songs results, and a result's More menu. Everything that
depends on how the Music window is laid out is in this module and nowhere else.

What it is allowed to do is deliberately narrow: search the Apple Music catalog, read
the song results, and choose "Add to Library" for one result. It never chooses any
other menu item.

The layout below was read from Music 1.6.3 on macOS 26.3; ARCHITECTURE.md records it.
Each script checks that the elements it needs exist before it types or presses
anything, and fails with LAYOUT_HINT if they do not.

Two facts shape everything here:

- Accessibility can only see Music's window while Music is the frontmost app on the
  visible desktop, so every operation brings Music to the front.
- The search field ignores a value assigned to it; it has to be typed into. Typing
  goes to whatever has the keyboard, so the script confirms that Music is frontmost
  and the field is focused immediately before it types, and checks what the field
  holds before pressing Return.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from .music_app import (
    ACCESSIBILITY_HELP,
    AccessibilityPermissionError,
    MusicAppError,
    ScriptRunner,
    run_osascript,
)

LAYOUT_HINT = (
    "The Music window may have changed with an update. To see what is there now, run: "
    "python -m daily_mix_sync experimental-ui-inspect"
)

# Error numbers raised by the scripts below.
_ERR_NO_WINDOW = 9101  # Music's window is not in front
_ERR_LAYOUT = 9102  # an element the script relies on is missing
_ERR_KEYBOARD = 9103  # the keyboard was not where it had to be; nothing was typed
_ERR_TIMEOUT = 9104  # results did not arrive in time
_ERR_ROW_GONE = 9105  # the chosen result is no longer on screen
_ERR_NO_ADD = 9106  # the result's menu does not offer Add to Library

_US = "\x1f"
_RS = "\x1e"
_OBJECT_REPLACEMENT = "￼"  # where the window shows an inline badge next to a title
_SONG_ID = re.compile(r"TrackLockup\[id=track-section-song-([^,\]]+)")


class MusicUIError(MusicAppError):
    """Operating the Music window failed for this one request."""


class MusicUILayoutError(MusicUIError):
    """The Music window is not laid out the way this module expects."""


@dataclass(frozen=True)
class CatalogUIResult:
    """One song in the Apple Music search results currently on screen.

    A navigation handle, not an identity: `element_id` finds the same row again
    while those results are showing and means nothing afterwards. The results page
    shows neither album nor duration.
    """

    title: str
    artist: str
    ordinal: int  # position among the song results, from 1
    element_id: str  # the row's accessibility identifier

    @property
    def catalog_id(self) -> str | None:
        """The Apple Music ID embedded in the row's identifier, if it has one."""
        match = _SONG_ID.search(self.element_id)
        return match.group(1) if match else None


# --- AppleScript ---------------------------------------------------------------
#
# The only places that name parts of the Music window. Handlers return references
# to elements or raise error 9102 saying which one is missing.

_HELPERS = """
property US : character id 31
property RS : character id 30

on joined(parts, separator)
	set AppleScript's text item delimiters to separator
	set out to parts as text
	set AppleScript's text item delimiters to ""
	return out
end joined

on identifierOf(el)
	tell application "System Events"
		try
			set v to value of attribute "AXIdentifier" of el
			if v is missing value then return ""
			return v as text
		on error
			return ""
		end try
	end tell
end identifierOf

-- Bring Music to the front and return its main window.
on musicWindow()
	tell application "Music" to activate
	tell application "System Events"
		set waited to 0
		repeat
			set ready to false
			try
				set ready to (frontmost of process "Music") and ((count of windows of process "Music") > 0)
			end try
			if ready then exit repeat
			if waited >= 50 then error "Music's window did not come to the front within 5 seconds." number 9101
			if waited mod 10 is 9 then tell application "Music" to activate
			delay 0.1
			set waited to waited + 1
		end repeat
		delay 0.2
		tell process "Music"
			repeat with i from 1 to count of windows
				set ok to false
				try
					set ok to (subrole of window i is "AXStandardWindow") and ((count of splitter groups of window i) > 0) and ((count of toolbars of window i) > 0)
				end try
				if ok then return window i
			end repeat
		end tell
	end tell
	error "Music's main window (a window with a sidebar and a toolbar) was not found." number 9102
end musicWindow

on musicIsFrontmost()
	tell application "System Events" to return frontmost of process "Music"
end musicIsFrontmost

on sidebarOutline(win)
	tell application "System Events" to tell process "Music"
		set sg to splitter group 1 of win
		repeat with i from 1 to count of scroll areas of sg
			if my identifierOf(scroll area i of sg) is "sidebarScroller" then
				if (count of outlines of scroll area i of sg) > 0 then return outline 1 of scroll area i of sg
			end if
		end repeat
	end tell
	error "The sidebar was not found in Music's window." number 9102
end sidebarOutline

-- The pane that shows search results: the scroll area that is not the sidebar.
on contentArea(win)
	tell application "System Events" to tell process "Music"
		set sg to splitter group 1 of win
		repeat with i from 1 to count of scroll areas of sg
			if my identifierOf(scroll area i of sg) is not "sidebarScroller" then return scroll area i of sg
		end repeat
	end tell
	error "The main pane was not found in Music's window." number 9102
end contentArea

on searchField(win)
	tell application "System Events" to tell process "Music"
		set tb to toolbar 1 of win
		repeat with i from 1 to count of groups of tb
			repeat with j from 1 to count of text fields of group i of tb
				if subrole of text field j of group i of tb is "AXSearchField" then return text field j of group i of tb
			end repeat
		end repeat
	end tell
	error "The search field was not found in Music's toolbar." number 9102
end searchField

on scopeSelector(win)
	tell application "System Events" to tell process "Music"
		set tb to toolbar 1 of win
		repeat with i from 1 to count of groups of tb
			repeat with j from 1 to count of radio groups of group i of tb
				if my identifierOf(radio group j of group i of tb) is "UIA.Music.Search.Scope" then return radio group j of group i of tb
			end repeat
		end repeat
	end tell
	error "The search scope selector (Apple Music / Library) was not found in Music's toolbar." number 9102
end scopeSelector

-- Select "Search" in the sidebar and wait for the search controls to show.
on showSearchPage(win)
	set o to my sidebarOutline(win)
	tell application "System Events" to tell process "Music"
		set target to missing value
		repeat with i from 1 to count of rows of o
			if i > 12 then exit repeat
			set label to ""
			try
				set label to name of UI element 1 of row i of o
			end try
			if label is "Search" then
				set target to row i of o
				exit repeat
			end if
		end repeat
		if target is missing value then error "Music's sidebar has no Search entry." number 9102
		if not (selected of target) then set selected of target to true
	end tell
	repeat 40 times
		try
			my searchField(win)
			my scopeSelector(win)
			return
		end try
		delay 0.1
	end repeat
	error "Selecting Search in the sidebar did not bring up the search field and scope selector." number 9102
end showSearchPage

on chooseAppleMusicScope(win)
	set scope to my scopeSelector(win)
	tell application "System Events" to tell process "Music"
		set target to missing value
		repeat with i from 1 to count of radio buttons of scope
			if description of radio button i of scope is "Apple Music" then set target to radio button i of scope
		end repeat
		if target is missing value then error "The search scope selector has no Apple Music option." number 9102
		if (value of target) is 1 then return
		perform action "AXPress" of target
		repeat 30 times
			if (value of target) is 1 then return
			delay 0.1
		end repeat
	end tell
	error "The search scope could not be switched to Apple Music." number 9102
end chooseAppleMusicScope

-- A result section by its heading ("Songs", "Top Results"), or missing value.
on sectionNamed(content, heading)
	tell application "System Events" to tell process "Music"
		try
			if (count of lists of content) is 0 then return missing value
			set outer to list 1 of content
			repeat with i from 1 to count of lists of outer
				set d to ""
				try
					set d to description of list i of outer
				end try
				if d is heading then return list i of outer
			end repeat
		end try
	end tell
	return missing value
end sectionNamed

-- Identifiers of what the page shows first: changes when new results arrive.
on pageSignature(content)
	set ids to {}
	tell application "System Events" to tell process "Music"
		try
			set outer to list 1 of content
			set firstSection to list 1 of outer
			repeat with i from 1 to count of UI elements of firstSection
				set end of ids to my identifierOf(UI element i of firstSection)
			end repeat
		end try
	end tell
	return my joined(ids, "|")
end pageSignature

-- The song rows of the Songs section, as (identifier, title, artist) lines.
-- (Inside a System Events tell block, "rows" would mean the window's table rows.)
on songRows(songs)
	set songLines to {}
	tell application "System Events" to tell process "Music"
		repeat with i from 1 to count of groups of songs
			set g to group i of songs
			set ident to my identifierOf(g)
			if ident contains "TrackLockup" then
				set artistName to ""
				repeat with j from 1 to count of buttons of g
					set d to ""
					try
						set d to description of button j of g
					end try
					if d is not "More" then
						try
							set artistName to title of button j of g
						end try
						if artistName is not "" then exit repeat
					end if
				end repeat
				set end of songLines to ident & US & (description of g) & US & artistName
			end if
		end repeat
	end tell
	return my joined(songLines, RS)
end songRows
"""

_UI_ELEMENTS_ENABLED = 'tell application "System Events" to return UI elements enabled'
_FRONTMOST_APP = (
    'tell application "System Events" to return name of first application process '
    "whose frontmost is true"
)
_ACTIVATE = """
on run argv
	tell application (item 1 of argv) to activate
end run
"""

# argv: query, seconds to wait for results.
# Replies with the song rows; an empty reply means the search found no songs.
_SEARCH = _HELPERS + """
on run argv
	set query to item 1 of argv
	set patience to (item 2 of argv) as integer
	set win to my musicWindow()
	my showSearchPage(win)
	my chooseAppleMusicScope(win)
	set field to my searchField(win)
	set content to my contentArea(win)

	tell application "System Events" to tell process "Music"
		set showing to value of field
		if showing is missing value then set showing to ""
	end tell
	set haveResults to (my sectionNamed(content, "Top Results") is not missing value) or (my sectionNamed(content, "Songs") is not missing value)
	if not (showing is query and haveResults) then
		set oldSignature to my pageSignature(content)
		my typeQuery(field, query)
		set startedAt to current date
		set unchangedSince to missing value
		repeat
			delay 0.2
			set haveResults to (my sectionNamed(content, "Top Results") is not missing value) or (my sectionNamed(content, "Songs") is not missing value)
			if haveResults then
				if my pageSignature(content) is not oldSignature then exit repeat
				-- The page shows what it showed before. Either this search has the
				-- same results as the last one, or the new ones are slow. Allow a
				-- few seconds, then read what is there: each row is scored against
				-- the track being looked for, so a stale page can cause a miss but
				-- never a wrong choice.
				if unchangedSince is missing value then set unchangedSince to current date
				if ((current date) - unchangedSince) >= 4 then exit repeat
			end if
			if ((current date) - startedAt) >= patience then error "Apple Music showed no results for " & quote & query & quote & " within " & patience & " seconds." number 9104
		end repeat
	end if

	-- The Songs section only exists once the page has been scrolled.
	set songs to my sectionNamed(content, "Songs")
	if songs is missing value then
		tell application "System Events" to tell process "Music"
			repeat 30 times
				if (count of scroll bars of content) > 0 then exit repeat
				delay 0.1
			end repeat
			if (count of scroll bars of content) > 0 then set value of scroll bar 1 of content to 0.5
		end tell
		repeat 40 times
			set songs to my sectionNamed(content, "Songs")
			if songs is not missing value then exit repeat
			delay 0.1
		end repeat
	end if
	if songs is missing value then return ""
	return my songRows(songs)
end run

-- Put the query in the search field and submit it. Keystrokes go to whatever has
-- the keyboard, so nothing is typed unless that is Music's search field.
on typeQuery(field, query)
	tell application "System Events" to tell process "Music"
		set focused of field to true
		repeat 20 times
			if focused of field then exit repeat
			delay 0.1
		end repeat
		if not ((focused of field) and frontmost) then error "The search field could not be given the keyboard; nothing was typed." number 9103
		set value of field to ""
		repeat 20 times
			if (value of field) is "" then exit repeat
			delay 0.1
		end repeat
		if not ((focused of field) and frontmost) then error "Music lost the keyboard before the search could be typed; nothing was typed." number 9103
		keystroke query
		repeat 30 times
			if (value of field) is query then exit repeat
			delay 0.1
		end repeat
		if (value of field) is not query then error "The search field holds " & quote & (value of field) & quote & " instead of the query; the search was not submitted." number 9103
		if not ((focused of field) and frontmost) then error "Music lost the keyboard before the search could be submitted." number 9103
		key code 36
	end tell
end typeQuery

"""

# No argv. Reads the window and reports what the automation relies on, one line per
# item. It selects Search in the sidebar so the search controls can be checked, and
# presses and types nothing else.
_INSPECT = _HELPERS + """
on run argv
	set report to {}
	set win to my musicWindow()
	tell application "System Events" to tell process "Music"
		set end of report to "ok" & US & "main window" & US & (name of win) & ", " & (count of windows) & " window(s) in all"
	end tell
	try
		set o to my sidebarOutline(win)
		tell application "System Events" to tell process "Music" to set n to count of rows of o
		set end of report to "ok" & US & "sidebar" & US & (n as text) & " rows"
	on error message
		set end of report to "missing" & US & "sidebar" & US & message
	end try
	try
		my showSearchPage(win)
		set end of report to "ok" & US & "Search entry in the sidebar" & US & "selected"
	on error message
		set end of report to "missing" & US & "Search entry in the sidebar" & US & message
	end try
	try
		set field to my searchField(win)
		tell application "System Events" to tell process "Music"
			set shown to value of field
			if shown is missing value then set shown to ""
			set hint to ""
			try
				set hint to value of attribute "AXPlaceholderValue" of field
			end try
		end tell
		set end of report to "ok" & US & "search field" & US & "holds " & quote & shown & quote & ", placeholder " & quote & hint & quote
	on error message
		set end of report to "missing" & US & "search field" & US & message
	end try
	try
		set scope to my scopeSelector(win)
		set options to {}
		tell application "System Events" to tell process "Music"
			repeat with i from 1 to count of radio buttons of scope
				set label to description of radio button i of scope
				if (value of radio button i of scope) is 1 then set label to label & " (selected)"
				set end of options to label
			end repeat
		end tell
		set end of report to "ok" & US & "search scope selector" & US & my joined(options, ", ")
	on error message
		set end of report to "missing" & US & "search scope selector" & US & message
	end try
	try
		set content to my contentArea(win)
		set headings to {}
		tell application "System Events" to tell process "Music"
			if (count of lists of content) > 0 then
				set outer to list 1 of content
				repeat with i from 1 to count of lists of outer
					set d to "(unnamed)"
					try
						set d to description of list i of outer
					end try
					set end of headings to d & " (" & (count of UI elements of list i of outer) & ")"
				end repeat
			end if
		end tell
		if (count of headings) is 0 then set headings to {"none"}
		set end of report to "ok" & US & "main pane" & US & "sections: " & my joined(headings, ", ")
		set songs to my sectionNamed(content, "Songs")
		if songs is missing value then
			set end of report to "info" & US & "Songs section" & US & "not present (it appears after a search, once the page has been scrolled)"
		else
			set end of report to "ok" & US & "Songs section" & US & "present"
			set end of report to "rows" & US & "song rows" & US & my songRows(songs)
		end if
	on error message
		set end of report to "missing" & US & "main pane" & US & message
	end try
	return my joined(report, linefeed)
end run
"""

# argv: the row's identifier, and "add" or "check".
# Opens the row's More menu and reads it. In "add" mode it chooses Add to Library if
# that is offered and replies "added". Otherwise it closes the menu without choosing
# anything and replies "present" (the menu shows the song is in the library) or, in
# "check" mode, "absent". Add to Library is the only menu item this ever chooses.
_MENU = _HELPERS + """
on run argv
	set wantedID to item 1 of argv
	set mode to item 2 of argv
	set win to my musicWindow()
	set content to my contentArea(win)
	set songs to my sectionNamed(content, "Songs")
	if songs is missing value then error "The song results are no longer on screen." number 9105
	tell application "System Events" to tell process "Music"
		set target to missing value
		repeat with i from 1 to count of groups of songs
			if my identifierOf(group i of songs) is wantedID then
				set target to group i of songs
				exit repeat
			end if
		end repeat
		if target is missing value then error "The chosen song is no longer among the results on screen." number 9105
		set moreButton to missing value
		repeat with j from 1 to count of buttons of target
			set d to ""
			try
				set d to description of button j of target
			end try
			if d is "More" then set moreButton to button j of target
		end repeat
		if moreButton is missing value then error "The song result has no More button." number 9102
		try
			perform action "AXScrollToVisible" of songs
		end try
		delay 0.2
		perform action "AXPress" of moreButton
		repeat 30 times
			if (count of menus of target) > 0 then exit repeat
			delay 0.1
		end repeat
		if (count of menus of target) is 0 then error "The song result's More menu did not open." number 9102
		set theMenu to menu 1 of target
		set itemNames to {}
		set addItem to missing value
		repeat with k from 1 to count of menu items of theMenu
			set itemName to name of menu item k of theMenu
			if itemName is not missing value then
				set end of itemNames to itemName
				if itemName is "Add to Library" then set addItem to menu item k of theMenu
			end if
		end repeat
		if addItem is not missing value and mode is "add" then
			if not (enabled of addItem) then
				perform action "AXCancel" of theMenu
				error "Add to Library is offered but not available for this song right now." number 9106
			end if
			perform action "AXPress" of addItem
			repeat 30 times
				if (count of menus of target) is 0 then exit repeat
				delay 0.1
			end repeat
			return "added"
		end if
		perform action "AXCancel" of theMenu
		repeat 30 times
			if (count of menus of target) is 0 then exit repeat
			delay 0.1
		end repeat
		if addItem is not missing value then return "absent"
		if itemNames contains "Delete from Library" then return "present"
		error "The song result's menu offers neither Add to Library nor Delete from Library (it offers: " & my joined(itemNames, ", ") & ")." number 9106
	end tell
end run
"""

# argv: which part ("toolbar" or "pane"). Replies with every element under it.
_DUMP = _HELPERS + """
on run argv
	set win to my musicWindow()
	tell application "System Events" to tell process "Music"
		if (item 1 of argv) is "toolbar" then
			return entire contents of toolbar 1 of win
		end if
	end tell
	set content to my contentArea(win)
	tell application "System Events" to tell process "Music" to return entire contents of content
end run
"""


# --- Python side ---------------------------------------------------------------


def accessibility_allowed(run: ScriptRunner = run_osascript) -> bool:
    """Whether macOS will let this program read and operate the Music window."""
    return run(_UI_ELEMENTS_ENABLED, []) == "true"


def _clean(text: str) -> str:
    return " ".join(text.replace(_OBJECT_REPLACEMENT, " ").split())


def _translate(error: MusicAppError) -> MusicAppError:
    """Turn a failed UI script into the exception that says what kind of failure it was."""
    if isinstance(error, AccessibilityPermissionError):
        return error
    message = re.sub(r" \(AppleScript error -?\d+\)$", "", str(error))
    if error.code in (_ERR_LAYOUT, _ERR_NO_WINDOW):
        return MusicUILayoutError(f"{message} {LAYOUT_HINT}")
    if error.code in (_ERR_KEYBOARD, _ERR_TIMEOUT, _ERR_ROW_GONE, _ERR_NO_ADD):
        return MusicUIError(message)
    if error.code in (-1719, -1728):
        # System Events could not find something the script addressed directly.
        return MusicUILayoutError(f"An expected part of the Music window is missing: {message} {LAYOUT_HINT}")
    return error


class MusicCatalogUI:
    """The Apple Music catalog, as reachable through the Music window."""

    def __init__(self, run: ScriptRunner = run_osascript, results_timeout_s: int = 15) -> None:
        self._run = run
        self.results_timeout_s = results_timeout_s

    def _ui(self, script: str, args: list[str]) -> str:
        try:
            return self._run(script, args)
        except MusicAppError as error:
            raise _translate(error) from error

    def accessibility_allowed(self) -> bool:
        return accessibility_allowed(self._run)

    def require_accessibility(self) -> None:
        """Raise unless macOS lets this program operate other apps' windows."""
        if not self.accessibility_allowed():
            raise AccessibilityPermissionError(
                "Searching the Apple Music catalog needs macOS Accessibility permission. "
                + ACCESSIBILITY_HELP
            )

    @contextmanager
    def session(self) -> Iterator[None]:
        """Do UI work, then put the app that was in front back in front.

        Music has to be frontmost for any of this, so it takes over the screen
        while the block runs.
        """
        self._previous_app = ""
        try:
            self._previous_app = self._run(_FRONTMOST_APP, [])
        except MusicAppError:
            pass  # not worth failing over; Music will simply stay in front
        try:
            yield
        finally:
            self.hand_back()
            self._previous_app = ""

    def hand_back(self) -> None:
        """Bring back the app that was in front when the session began.

        Used at the end of a session, and in the middle of one when the person has
        to answer a question somewhere else. The next UI call brings Music forward
        again by itself.
        """
        previous = getattr(self, "_previous_app", "")
        if previous and previous != "Music":
            try:
                self._run(_ACTIVATE, [previous])
            except MusicAppError:
                pass

    def search_catalog(self, term: str) -> list[CatalogUIResult]:
        """Search Apple Music for `term` and return the song results, in page order.

        Reads the first screen of the Songs section, which is what a person would
        see without choosing "see all". Changes nothing in the library.
        """
        term = " ".join(term.split())
        if not term:
            return []
        reply = self._ui(_SEARCH, [term, str(self.results_timeout_s)])
        return _results(reply)

    def in_library(self, result: CatalogUIResult) -> bool:
        """Whether Music's own menu says this result is in the library. Changes nothing."""
        return self._ui(_MENU, [result.element_id, "check"]) == "present"

    def add_to_library(self, result: CatalogUIResult) -> bool:
        """Choose Add to Library for one result that is on screen.

        Returns False, having changed nothing, if Music shows the song is already
        in the library. Raises MusicUIError if Add to Library is not offered.
        """
        return self._ui(_MENU, [result.element_id, "add"]) == "added"

    def inspect(self) -> list[tuple[str, str, str]]:
        """What the automation relies on and whether it is there: (state, item, detail)."""
        lines = self._ui(_INSPECT, []).split("\n")
        report = []
        for line in lines:
            state, _, rest = line.partition(_US)
            item, _, detail = rest.partition(_US)
            if state == "rows":
                found = _results(detail)
                detail = "; ".join(f"{r.title} — {r.artist}" for r in found) or "none"
                state = "ok"
            report.append((state, item, detail))
        return report

    def dump(self, part: str) -> str:
        """Every element under the toolbar or the main pane, as System Events names them."""
        return self._ui(_DUMP, [part])


def _results(reply: str) -> list[CatalogUIResult]:
    results = []
    for row in reply.split(_RS) if reply else []:
        fields = row.split(_US)
        if len(fields) != 3:
            raise MusicUILayoutError(f"Unexpected song row from the Music window: {row!r}. {LAYOUT_HINT}")
        element_id, title, artist = fields
        results.append(
            CatalogUIResult(
                title=_clean(title),
                artist=_clean(artist),
                ordinal=len(results) + 1,
                element_id=element_id,
            )
        )
    return results
