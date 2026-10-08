# Architecture

One Python package, one SQLite file, and one small browser extension; no services, no
credentials. Each stage is a module with plain functions and dataclasses, so any stage
can be run and tested on its own.

The two halves meet in one place only: the extension writes a playlist export, a JSON
file, and the Python application reads it. Neither calls the other.

The supported workflow is **library-only**: a Daily Mix is recreated from the songs
that are already in the Apple Music library, and the rest are left out. Nothing is
added to the library, and Music is reached through AppleScript alone.

## Pipeline

```
Spotify web player                extension/      read the page, scroll, collect by position
        │                         (Chrome)        → download daily_mix_1.json
        ▼
playlist export (JSON)            importer.py     validate, drop duplicates
        │
        ▼
   normalize                      normalize.py    base title, flags, qualifiers, artists
        │
        ▼
   cache lookup                   database.py     source key → remembered Music track
        │                         music_app.py    is that track still in the library?
        │ miss, or track gone          └── yes ──► include (CACHED)
        ▼
   local Music library search     sync.py         1. title + primary artist, first 60 songs
        │                         music_app.py    2. only if that gave no match: title alone,
        │                         (AppleScript)      in song titles, first 25
        │                                         merged, each song once, step 1 first
        ▼
   candidate scoring              matcher.py      0–100 per candidate, best first; the same
        │                                         for every candidate, however it was found
        │
        ├── matched  (≥ 90) ──► include, remember           MATCHED
        ├── review   (≥ 75) ──► manual choice   review.py   MANUAL if picked, else left out
        └── not in library ───► skip                        FAILED
        ▼
   every track included or left out; no playlist has changed yet
        │
        ▼
   managed playlist update        sync.py         record contents → empty → add in order
                                  music_app.py    → read back and compare → restore on failure
```

A track that is not in the library is an expected outcome, not an error. Its internal
status is still called `FAILED` (the matcher did not produce a match), but against the
library every report words it as "not in library" and the run succeeds without it.

`sync.py` holds the matching loop, which works with anything that can search for
songs, and the playlist write, which needs the real Music app. `cli.py` parses
arguments, asks the questions and prints the report; `config.py` loads thresholds,
weights and the managed playlist prefix; `models.py` holds the records.

None of the modules on this path imports `music_ui.py`, `catalog.py` or
`experimental.py`, and a test fails if one ever does.

Still to do: the Spotify extractor.

## Modules

| Module | Responsibility | Depends on |
| --- | --- | --- |
| `models.py` | `SourceTrack`, `AppleCandidate`, `Mapping` | nothing |
| `normalize.py` | Text cleanup, title/album/artist parsing, cache key | models |
| `matcher.py` | `MatchConfig`, scoring, accept / review / fail decision | normalize, rapidfuzz |
| `music_app.py` | `MusicApp`: everything said to Music, as AppleScript run by `osascript` | models |
| `apple_music.py` | The search seam and `MockCatalog`, its offline implementation | models, normalize |
| `review.py` | Asking a person to pick among candidates; stores the pick as a manual mapping | matcher, database |
| `database.py` | `MappingStore`: SQLite mapping cache | models, normalize |
| `importer.py` | Load and validate a playlist export | models, normalize |
| `config.py` | `Settings` from defaults plus optional JSON file | matcher, music_app |
| `sync.py` | The matching loop, destination naming, and the playlist write with verification and restore | all of the above |
| `cli.py` | The supported commands and their output; registers the experimental ones | all of the above |

Experimental, off the main path (see
[Experimental subsystem](#experimental-subsystem-apple-music-catalog)):

| Module | Responsibility | Depends on |
| --- | --- | --- |
| `music_ui.py` | `MusicCatalogUI`: everything that depends on the layout of the Music window | music_app |
| `catalog.py` | Resolving a track through the catalog: search, score, add, wait for the library, check | sync, music_ui, music_app, matcher, database |
| `experimental.py` | The `experimental-*` commands, and the only way into the two modules above | cli, catalog, music_ui |

The matcher never touches Music or the database: it takes a track and a list of
candidates and returns a decision. That is what makes it testable without Music.

The one seam is two methods: `search_songs(term, limit, titles_only=False)` to find
candidates, and `get_track(persistent_id)` to check that a remembered track still
exists. `MusicApp` implements them over the local library and `MockCatalog` over a
JSON fixture; the matching loop cannot tell them apart, so `match` and `review` run
the same code against either. That seam is also where a future source of songs would
plug in, without the pipeline changing.

### Search finds candidates; the matcher accepts them

`sync.match_one` is the only place that decides what to search for. It searches by
title and primary artist; if no candidate from that is an automatic match, it searches
again by title alone, in song titles only, and adds whatever is new. The two lists are
merged by persistent ID with the first search's results in front, which matters
because the matcher's sort is stable: on equal scores the more specific search wins.

The division of labour is strict. Search is allowed to be generous, because it decides
only what gets looked at; `matcher.match_track` then scores every candidate the same
way against the same thresholds, with no knowledge of which search produced it. Making
the search wider therefore cannot make a match looser, and the tests for the second
search are mostly tests that wrong versions it turns up are still rejected.

Why two searches and those depths: Music returns results in library order rather than
by relevance, and requires every query word to be present. The first makes depth
matter (a title track sits behind its album); the second makes artist words a risk
(one word the library spells differently hides the song). The depths, 60 and 25, were
measured on a real library and are settings; the README has the table. Each search
result is read with a single request for all its properties, which is about six times
faster than asking for each one, and is what makes a depth of 60 affordable. A result
Music cannot describe is skipped inside the script rather than failing the search.

A match result records the title query, if one was made, and which candidates only it
found, so the command line can say so.

## Spotify export extension

`extension/` is a Manifest V3 Chrome extension in plain JavaScript: no framework, no
build step, no dependencies. Its own README covers use, permissions and the selectors;
this is how it is put together and why.

| File | Responsibility | Touches |
| --- | --- | --- |
| `export_format.js` | Pure functions: duration, track ID and URL, file name, merging rows, the JSON | nothing |
| `spotify_dom.js` | Every selector, and reading a name, a count or a row from the page | reads the DOM |
| `content.js` | `scanPlaylist`: the scroll-and-collect loop; and the link to the popup | reads the DOM, sets `scrollTop` |
| `popup.html`, `popup.js` | The states the user sees; injects the three files above; saves the file | `chrome.*`, its own page |
| `manifest.json` | `activeTab` and `scripting`, an action with a popup, nothing else | |

The three page files are ordinary scripts that hang their functions on one shared
object, so the same files run in the tab, in the popup and under Node for the tests,
with nothing to bundle. Nothing in them uses a global `document`: they are handed the
document, a clock and a `sleep`, which is what lets the tests drive them.

### Why rows are collected by position

Spotify keeps only the rows near the viewport in the page and swaps them as it
scrolls, so the list has to be scrolled and read piece by piece. The obvious way to
merge the pieces, by track ID, goes wrong twice: it drops a song that is in the
playlist two times, and it cannot tell whether anything was missed.

Each row carries `aria-rowindex`, its place in the list, and the list carries
`aria-rowcount`. So rows go into a map keyed by position. Reading a row again is
harmless, the order is exact, and completeness is a fact that can be checked: every
position from 1 to the count is there, or the export is refused. The loop is written
around the one row it still needs:

```
read every row in the page into the map
need = lowest position not read yet           none left → done
need below what the page shows → scroll down one step
need above it → scroll up one step (straight to the top for row 1)
no rows in the page → still loading: wait      (for row 1: look from the top down)
pause, then wait for a redraw; a page that does not redraw in time gets longer next time
no new row for 10 seconds → fail, naming the row
```

A step is 80% of the visible height, so consecutive views overlap. Every pass pauses
at least once, so the loop cannot spin and freeze the tab, and it ends either complete
or with an error; there is no third outcome. The scroll position is restored in a
`finally`, whether the scan succeeded, failed or was cancelled by closing the popup.

Duplicate songs are deliberately left in. Dropping repeats is the importer's job, and
it reports what it dropped.

### Why `activeTab` and not access to Spotify

With `activeTab` and `scripting`, the extension has no standing access to any site.
Clicking its button grants access to that one tab; the popup then checks the address
and injects the scripts. The alternative, a content script declared for
`open.spotify.com`, would run on every Spotify page whether or not an export was
wanted. The download uses a link in the popup, which needs no permission.

### Read-only, enforced

The scripts that run in the Spotify tab may read the page and assign `scrollTop`,
and that is all. `tests/extension/extension_files.test.js` reads their source and
fails on a click, a focus, a dispatched event, a network call, a storage or cookie
read, any change to the page, any assigned property other than `scrollTop`, and any
extension API beyond the connection to the popup. A second test runs a whole scan
against the simulated page with those methods instrumented and checks the page is
byte for byte as it was found.

### When the markup is not what was expected

Every step that depends on the page has its own failure, carrying the name of the
selector that found nothing: no track list, no playlist name, no row count, rows
without a position, no readable rows. A row that is recognisably something else (a
podcast episode, a local file) is left out and reported rather than guessed at. The
debug text records each step with its timing, how many elements each selector matches,
and the markup of one row with class names removed.

No generated class name is used: selectors are `data-testid` values, ARIA roles and
attributes, and link addresses, and the scrolling element is found by its computed
`overflow-y`.

### The contract with the importer

`tests/extension/fixtures/expected_export.json` was written independently of the
extension, from the list of songs the fixture page is meant to hold. The JavaScript
tests require the extension's
export of that page to equal it byte for byte; `tests/test_extension_export.py` loads
it through `load_playlist` and requires every track to arrive with nothing skipped or
warned about. A change to the format on either side breaks one of the two.

## Music app layer

`music_app.py` is the only place that knows AppleScript or Music's scripting
dictionary. The rest of the program calls methods such as `search_songs`,
`ensure_playlist`, `add_tracks` and `clear_playlist` and does not know how they are
carried out.

How it is built:

- **Scripts are constants; values travel as arguments.** Each script starts with
  `on run argv` and is run as `osascript -e SCRIPT -- ARG...`. Titles and names are
  never pasted into script text, so quotes and odd characters cannot break or alter a
  script. The `--` keeps an argument such as `-1` from being read as an option.
- **Replies are rows of fields** separated by the ASCII record and unit separators,
  parsed in Python. A reply with the wrong shape is an error, not a guess.
- **Errors are translated.** osascript's error number decides the exception:
  `MusicPermissionError` for a missing Automation permission (-1743),
  `AccessibilityPermissionError` when System Events reports "not allowed assistive
  access", `UnmanagedPlaylistError` for the safety check, `MusicAppError` for the rest.
- **The script runner is injectable.** `MusicApp(run=...)` takes any callable in place
  of `osascript`, which is how the unit tests run the real adapter code without Music.

Behaviours of the installed Music app that the code depends on, each found by trying
it:

| Observation | Consequence in the code |
| --- | --- |
| `search` needs every word to match (as a word prefix, in any field including composer, any order) and ignores case, accents and punctuation | Query is base title + primary artist; the word "and" is dropped |
| An empty search term is error -50 | Blank searches are answered without calling Music |
| Looking a playlist up by name ignores case | Names are compared again, exactly, in Python and in the script |
| `user playlists` includes smart playlists, folders and Music's built-in lists | Only class `user playlist`, not smart, special kind `none` counts |
| Asking an empty playlist for a property of every track is error -1728 | The track count is checked first |
| Inside `tell application "Music"`, `names`, `artists` and `albums` are constants | Script variables use other names |
| The library holds music videos as well as songs | Search keeps `media kind` song only |
| A track has one persistent ID, in the library and in every playlist | That ID is the cached mapping |
| Tracks of a playlist are reported in display order unless `fixed indexing` is on | It is switched on for the read and put back, so order checks mean something |
| A playlist deleted and recreated under the same name can come back from iCloud as a duplicate | Syncing empties and never deletes; the live tests never delete either |
| There is no command to search the Apple Music catalog or add a catalog song | Songs outside the library need UI automation |

### Safety rule

A playlist may be changed only if its name is the managed prefix (default
`Spotify Daily Mix`) or the prefix plus a space and more. The rule is enforced twice:

1. In Python, before anything is sent. An unmanaged name raises
   `UnmanagedPlaylistError` and no script runs.
2. In AppleScript, at the top of every script that changes something. The playlist is
   looked up by persistent ID and must still have the expected name, that name must be
   managed, and it must be an ordinary playlist. This covers a playlist being renamed
   between the lookup and the change.

Tracks are deleted only through a reference to the playlist, never through the
library, so removal affects the playlist alone. Unit tests check that every changing
script begins with the safety check and that no reading script contains a changing
command.

### Updating a playlist

`write_playlist` in `sync.py` replaces the contents of one managed playlist:

1. Find or create the playlist (refused unless the name is managed).
2. Record what it holds.
3. Empty it and add the new tracks in order, in one script call.
4. Read it back and compare with what was intended: count, tracks and order.
5. If step 3 raised, step 4 found a difference, a track had left the library in the
   meantime, or the user interrupted: empty it again, add the recorded contents, and
   check that too. Then raise `PlaylistWriteError`, which says whether the restore
   worked and carries the recorded contents in case it did not.

Emptying keeps the playlist's persistent ID, so it remains the same playlist. Music
has no transactions, so step 5 is a best effort, and the code says so rather than
claiming atomicity.

The command resolves every track before step 1. A failure while matching or reviewing
therefore cannot leave a playlist half-written, and a run where nothing resolves stops
without touching the playlist.

### Where a playlist goes

`destination_name` puts the managed prefix in front of the export's playlist name and
drops whatever the name shares with the end of the prefix: `Daily Mix 1` →
`Spotify Daily Mix 1`. Because the result always begins with the prefix, no export can
name a playlist outside the managed ones; `--into` is checked against the same rule
before anything else happens.

## Experimental subsystem: Apple Music catalog

Everything from here to [Normalization](#normalization) describes code that the
supported workflow does not use. It is kept for possible future work on songs that
are not in the library, and it is reachable only through the `experimental-*`
commands in `experimental.py`. It needs Accessibility permission, takes over the
screen, and can add songs to the Music library, none of which is true of `sync`.

Why it is not part of sync: the project's aim is a playlist of songs the user already
has, with the library left exactly as it is. The catalog path conflicts with that by
construction, and it is also the fragile part, since it depends on how the Music
window happens to be built.

`music_ui.py` is the only place that knows how the Music window is built. It talks to
System Events through the same `osascript` runner as `music_app.py`, with the same
rule that values travel as arguments and never as script text. Its interface is three
calls: `search_catalog(term)` returns the song results on screen,
`add_to_library(result)` chooses Add to Library for one of them, and
`in_library(result)` reads whether Music shows it as already added.

### Layout observed

Read from the accessibility tree of Music 1.6.3 on macOS 26.3, English. Names in
quotes are the element's accessibility description or title; `id` is its
`AXIdentifier`.

```
window "Music"                       AXWindow / AXStandardWindow
├─ splitter group
│  ├─ scroll area   id sidebarScroller
│  │  └─ outline    id outline
│  │     └─ row → cell named "Search"            first row; then Home, New, Radio, Library…
│  ├─ scroll area   (the main pane: the one that is not the sidebar)
│  │  ├─ list       AXList / AXCollectionList
│  │  │  ├─ list "Top Results"   AXSectionList   cells: id Music.shelfItem.TopSearchLockup[id=top-search-section-top-<id>,…]
│  │  │  ├─ list "Artists"
│  │  │  ├─ list "Albums"
│  │  │  ├─ list "Songs"         exists only once the page has been scrolled
│  │  │  │  ├─ group             header: button id Music.shelf.header[parentId=track-section-song,itemCount=50,itemKind=trackLockup]
│  │  │  │  ├─ group "<title>"   id Music.shelfItem.TrackLockup[id=track-section-song-<catalog id>,parentId=track-section-song]
│  │  │  │  │  ├─ static texts   two empty, then the title (sometimes followed by U+FFFC where a badge is drawn)
│  │  │  │  │  ├─ button         title = artist name
│  │  │  │  │  └─ button "More"  pressing it opens `menu 1` of this group
│  │  │  │  └─ groups with one unnamed button each (shelf paging)
│  │  │  └─ list "Playlists", "Radio Episodes", "Music Videos"
│  │  └─ scroll bar
│  └─ group         id playerToolbarContainer
└─ toolbar
   ├─ group → text field   AXTextField / AXSearchField; placeholder names the scope
   └─ group → radio group  id UIA.Music.Search.Scope
              └─ radio buttons (AXSegment) "Apple Music", "Library", "iTunes Store"; value 1 = selected
```

The More menu of a song result:

| Song is | Items |
| --- | --- |
| not in the library | **Add to Library**, Add to Playlist, Play Next, Create Station, Favorite, Suggest Less, Get Info, Show in iTunes Store, Share |
| in the library | Pin Song, Download, Add to Playlist, Play Next, Create Station, Favorite, Suggest Less, Get Info, Show Album in Library, Show in iTunes Store, Share, **Delete from Library** |

So the menu itself says whether a song is in the library, and "Add to Library" is
there to be chosen only when it is not.

### What was learned by trying

| Observation | Consequence in the code |
| --- | --- |
| System Events reports no windows for Music unless Music is the frontmost app on the visible desktop | Every script starts by activating Music and waiting for its window; a session puts the previous app back in front afterwards |
| The search field shows a value assigned to it but does not search for it | The query is typed with keystrokes and submitted with Return |
| Keystrokes go to whatever has the keyboard | Immediately before typing and before Return, the script checks that Music is frontmost and the field is focused, and before Return that the field holds exactly the query; otherwise it stops with nothing sent |
| Assigning an empty value does clear the field | The field is cleared that way, with no select-all or delete keystrokes |
| The scope segment responds to AXPress and reports value 1 when selected | The scope is switched to Apple Music and confirmed before anything is typed |
| After Return the result sections appear within a few seconds; the scroll bar slightly later | Results are waited for with a time limit (15 s), never a fixed sleep |
| The Songs section is not in the tree until the page has been scrolled; setting the scroll bar's value brings it in. `AXScrollDownByPage` did not | The scroll bar is moved once, then the section is waited for |
| A song row offers only AXPress, which would play it | Rows are never pressed; the More button is |
| The row identifier embeds Apple's ID for the song | A result is found again by that identifier, not by position. The ID is used for nothing else and is never stored |
| Retyping a query can produce results identical to those already showing | Unchanged results are accepted after a four-second grace period rather than treated as "nothing arrived" |
| Apple returns loose guesses even for nonsense queries | Every row is scored against the track; a guess is never added |
| A menu can be closed with AXCancel | When nothing is chosen, the menu is dismissed that way, without an Escape keystroke |
| Variable names such as `rows` and `path` have meanings of their own inside a System Events tell block | Script variables avoid them |

No screen coordinates, mouse movement or image matching are used anywhere.

### Limits the scripts keep to

- The only menu item ever chosen is the one named exactly "Add to Library". Unit tests
  read the script and fail if any other press appears in it.
- Every script checks that the parts it needs exist before it types or presses
  anything. A missing part raises `MusicUILayoutError` naming it, with a pointer to
  `experimental-ui-inspect`.
- Failures are sorted into two kinds. `MusicUILayoutError` means the window is not as
  expected, and ends the catalog step, since every later track would fail the same
  way. `MusicUIError` concerns one track (no results in time, the row gone, Add to
  Library not offered), which is reported and skipped.

### From catalog result to library track

`catalog.py` runs the sequence for one track and never stores anything the window
told it:

1. `look_up` searches and turns each row into an ordinary candidate with a title and
   an artist, scored by `match_track`. Album and duration are simply absent, which the
   matcher already handles by leaving those components out.
2. A result at or above the accept threshold is chosen. One in the review band is put
   to the user through `choose_catalog_result` in `experimental.py`; picking it authorises adding that
   song, and nothing is remembered yet.
3. `add_to_library` chooses Add to Library, or reports that Music shows the song as
   present.
4. `wait_for_library` searches the library through AppleScript about once a second up
   to `catalog_wait_s`.
5. What it waits for is a confident match. For an automatic choice, that is the export
   track scored against the library with full metadata. For a manual choice, it is the
   chosen result's own title and artist, because the user may have picked a different
   version on purpose.
6. Only then is the mapping stored, with the library track's persistent ID, as `auto`
   or `manual`. From then on it is an ordinary remembered library match; nothing
   records that it came from the catalog.

A song that was added but never confirmed is left out and not remembered, and the
report says it stays in the library. With `--dry-run` the sequence stops after step 2.

`experimental-catalog-fill` runs this for the tracks of an export that the library
lacks. It writes no playlist: once a song is in the library, an ordinary `sync` finds
it there like any other. That is the whole connection between the two sides, and it
runs through the library, not through code.

### Adding straight to a playlist: tried, not adopted

An experiment on 2026-10-07, with Music's "Add songs to Library when adding to
playlists" setting off, added a catalog song to `Spotify Daily Mix TEST` through the
More menu's Add to Playlist submenu, without it entering the library.

| Question | Finding |
| --- | --- |
| Does it work? | Yes. The playlist gained the track at once, with no dialog; the library did not change |
| What does AppleScript see? | A `shared track` with full title, artist, album and duration, cloud status `subscription`, a persistent ID and a database ID. `kind` and `album artist` are empty, unlike a library track |
| Where can it be found? | Only inside that playlist. Looking the ID up in the library or at application level finds nothing; searching the playlist finds it |
| Can the existing code read and verify it? | `playlist_tracks()` reads it and the order check passes. `has_track`/`get_track` report it missing, and `add_tracks` cannot re-add it, because both look in the library |
| Can AppleScript remove it? | Yes, with no effect on the library |
| Is its ID durable? | No. Removing it and adding the same catalog song again gave a different persistent ID and database ID, with identical metadata |

So the persistent ID of such a track names one playlist entry, not a song. Supporting
this would mean remembering something other than a persistent ID, updating playlists
without emptying them, verifying against the playlist instead of the library, a
restore that does not rely on re-adding from the library, and letting the window
automation choose a second menu item. Track order is the hard part: Music's
dictionary has no command to reorder tracks within a playlist. Whether AppleScript
can `duplicate` a playlist-only entry was not tested.

## Normalization

Spotify and Apple Music mostly disagree about the *qualifiers* attached to a title:
`- Remastered 2011` vs nothing, `(feat. X)` in the title vs `X` in the artist field,
` - Live` vs `(Live)`. A title is split into four parts:

| Part | Example input | Result |
| --- | --- | --- |
| Base title | `Hotel California - 2013 Remaster` | `hotel california` |
| Featured artists | `Song (feat. Guest)`, `Song (with Guest)` | moved to the artist list |
| Version flags | `(Live)`, `- Acoustic Version`, `[Skrillex Remix]`, `- Radio Edit`, `(Demo)`, `(Instrumental)`, `(Remastered 2011)` | `live`, `acoustic`, `remix`, `radio_edit`, `demo`, `instrumental`, `remaster` |
| Leftover qualifier words | `(Taylor's Version)`, `(Skrillex Remix)`, `(Pts. 1-5)` | `taylors`; `skrillex`; `pts 1 5` |

Rules that matter:

- A qualifier is a bracketed group or a ` - ` suffix. Flags are only looked for inside
  qualifiers, so `Live Forever` is not a live track.
- Only recognised noise is discarded: edition and packaging words such as `Deluxe
  Edition`, `Album Version`, `Bonus Track`, `- Single`. Anything unrecognised is kept
  as leftover qualifier words and compared, so it is never silently deleted.
- Text is casefolded, accents and punctuation are stripped, `&` becomes `and`,
  whitespace is collapsed.
- Artist credits are split into individual names (`A, B`, `A & B`, `A feat. B`), so
  the two services' different credit styles produce the same list.
- Albums go through the same parsing. An album marked as a remaster flags its tracks
  as remasters, because that marker sits in the title on one service and in the album
  name on the other.

## Scoring

```
score = weighted average of the available similarities − penalties      (0–100)
```

| Similarity | Weight | How |
| --- | --- | --- |
| Title | 45% | Fuzzy ratio of the base titles |
| Artist | 35% | Best-match coverage between the two name lists; a subset (`A` vs `A, B`) scores high but below an exact credit |
| Album | 10% | Fuzzy ratio of normalized album names |
| Duration | 10% | 100 within 3 s, falling linearly to 0 at 15 s |

If album or duration is missing on either side, that component is left out and the
other weights are rescaled.

| Penalty | Points |
| --- | --- |
| Version flag on one side only: live, remix, acoustic, demo, instrumental | 30 each |
| Radio edit on one side only | 15 |
| Remaster on one side only | 2 (same recording; only breaks ties) |
| Leftover qualifier words differ | up to 20, in proportion to how many differ |
| Duration differs by 30 s or more | 15 |

With title and artist identical, a single live/remix/acoustic mismatch gives at most
70, which is below the review threshold. An unrecognised qualifier on one side gives at
most 80, which lands in review. Equal scores keep the order the search returned.

All numbers are fields of `MatchConfig` and can be overridden in `config.json`.

## Mapping cache

- Key: `spotify:track:<id>` when the export has a Spotify ID, otherwise
  `meta:<artists>|<title>|<album>|<seconds>` built from normalized metadata.
- Value: the matched track's Music persistent ID, plus its title, artist and album so
  the table can be read by a person.
- A stored mapping is a cache hit only if `get_track` still finds its track; the
  live track is what gets used. If the track is gone, the mapping is logged, deleted
  and the track is matched from scratch against the library as it is now; if nothing
  suitable is there, it is left out. This also means a made-up mock ID could not
  survive in a real run even if one got into the real database.
- Automatic matches and manual picks are stored. Tracks that need review, were
  skipped, or failed are evaluated again on the next run.
- A manual mapping is never replaced by an automatic one; the rule is enforced in the
  SQL upsert, not just by the caller.
- Each mapping is committed as soon as it is made, so an interrupted run keeps its
  progress.
- Mock runs use a separate database file so made-up track IDs cannot reach a real
  playlist.

## Tests

- **Unit tests** (`tests/`) are deterministic and never touch Music. Everything that
  talks to Music is run against `tests/fake_music.py`, an in-memory stand-in for
  `osascript` that speaks the same reply format, imitates the behaviours listed above
  and can be told to fail on the n-th call of a given script. That is how the
  rollback paths are tested. A fixture also makes any attempt to start a real program
  from a unit test fail.
- **`tests/test_library_only.py`** guards the shape of the supported workflow: no
  module on the sync path imports the experimental code; `cli.py` does nothing with it
  but register its commands; `sync` has no option that adds songs; the supported
  commands run with tripwires in place of the window automation; a missing song is
  left out and the run still succeeds.
- **`tests/test_search_recall.py`** covers the two-step search with the real
  `MusicApp` code over the stand-in: a title track behind its album, an artist the
  library credits differently, merging without duplicates, the order on equal scores,
  the bounds on both searches. Most of its cases are about what a wider search must
  not do: versions and same-titled songs it turns up are still rejected by the
  matcher. It came out of an audit of a real Daily Mix and a real library; the songs
  in it are made up, apart from a few public ones named as cases to keep right.
- **Live tests** (`tests/integration/`, marker `music_app`) drive the real app through
  AppleScript and run only with `pytest --music-app`. They change one playlist,
  `Spotify Daily Mix TEST`, including a full library-only `sync` into it; they put its
  contents back afterwards, never delete it, and assert that the library and every
  other playlist are unchanged. `test_search_live.py` is the exception: it only
  searches, and can be run on its own when nothing may be touched.
- **Experimental.** The catalog code has unit tests against a stand-in for the Music
  window (`tests/fake_ui.py`), tests that read the window scripts as text for what
  they are allowed to do, and six read-only live tests behind their own switch,
  `pytest --music-ui`.
- **Extension** (`tests/extension/`, run with `npm test`). Node's built-in test
  runner, with jsdom as the only dependency. Rows are read from a fixture page
  modelled on Spotify's real markup. The scroll loop runs against
  `helpers/spotify_page.js`, a simulated page that holds only the rows near the
  viewport, redraws after a delay, can load rows late or never, and keeps its own
  clock, so slow pages and stalls are tested in milliseconds. That clock has a
  ceiling: a scan that would never end fails its test instead of hanging it.
- **Extension, live** (`npm run test:live`). The real extension, loaded into a hidden
  Chrome with a throwaway profile through the DevTools protocol, exporting public
  playlists from the real site and compared with the page's own metadata. It is to the
  extension what `--music-app` is to the Music adapter, with one gap: it is signed
  out, so it cannot open a Daily Mix.

## Deliberately absent

No server, no web framework, no ORM, no async, no plugin system, no Apple API client
and no credential handling. In the supported workflow: no UI automation, no
Accessibility permission, and no way to add a song to the library.

On the Spotify side: no Spotify login, OAuth or Web API, no background or scheduled
export, no local server or native messaging between the extension and Python, and no
"sync" button in the browser. The extension produces a file and stops.
