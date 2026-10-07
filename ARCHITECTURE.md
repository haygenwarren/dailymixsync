# Architecture

One Python package, one SQLite file, no services, no credentials. Each stage is a
module with plain functions and dataclasses, so any stage can be run and tested on
its own. Apple Music is reached only by driving the Music app on this Mac.

## Pipeline

```
playlist export (JSON)            importer.py     validate, drop duplicates
        │
        ▼
   SourceTrack ──► normalization  normalize.py    base title, flags, qualifiers, artists
        │
        ▼
   cache lookup                   database.py     source key → remembered Music track
        │                         music_app.py    is that track still in the library?
        │ miss, or track gone          └── yes ──► CACHED
        ▼
   local Music library search     music_app.py    search_songs(term, limit) → candidates
        │                         (AppleScript)
        ▼
   candidate scoring              matcher.py      0–100 per candidate, best first
        │
        ├── score ≥ 90 ─► remember (auto)            MATCHED
        ├── score ≥ 75 ─► manual review   review.py  MANUAL if picked (remembered), else REVIEW
        └── otherwise ──► not in the library         FAILED
        │                     └─► Music UI automation (planned)   music_ui.py
        ▼
   every track resolved or set aside; nothing in Music has changed yet
        │
        ▼
   managed playlist               sync.py         record contents → empty → add in order
                                  music_app.py    → read back and compare → restore on failure
```

`sync.py` holds both halves: the matching loop, which works with anything that can
search for songs, and the playlist write, which needs the real Music app. `cli.py`
parses arguments, asks the questions and prints the report; `config.py` loads
thresholds, weights and the managed playlist prefix; `models.py` holds the records.

Still to do: the UI automation for songs that are not in the library, and the Spotify
extractor.

## Modules

| Module | Responsibility | Depends on |
| --- | --- | --- |
| `models.py` | `SourceTrack`, `AppleCandidate`, `Mapping` | nothing |
| `normalize.py` | Text cleanup, title/album/artist parsing, cache key | models |
| `matcher.py` | `MatchConfig`, scoring, accept / review / fail decision | normalize, rapidfuzz |
| `music_app.py` | `MusicApp`: everything said to Music, as AppleScript run by `osascript` | models |
| `music_ui.py` | Operating the Music window through Accessibility. Only the permission check so far | music_app |
| `apple_music.py` | The search seam and `MockCatalog`, its offline implementation | models, normalize |
| `review.py` | Asking a person to pick among candidates; stores the pick as a manual mapping | matcher, database |
| `database.py` | `MappingStore`: SQLite mapping cache | models, normalize |
| `importer.py` | Load and validate a playlist export | models, normalize |
| `config.py` | `Settings` from defaults plus optional JSON file | matcher, music_app |
| `sync.py` | The matching loop, destination naming, and the playlist write with verification and restore | all of the above |
| `cli.py` | Commands and output | all of the above |

The matcher never touches Music or the database: it takes a track and a list of
candidates and returns a decision. That is what makes it testable without Music.

The one seam is two methods: `search_songs(term, limit)` to find candidates, and
`get_track(persistent_id)` to check that a remembered track still exists. `MusicApp`
implements them over the local library and `MockCatalog` over a JSON fixture; the
matching loop cannot tell them apart, so `match` and `review` run the same code
against either.

## Music app layer

`music_app.py` is the only place that knows AppleScript or Music's scripting
dictionary. The rest of the program calls methods such as `search_songs`,
`ensure_playlist`, `add_tracks` and `clear_playlist` and does not know how they are
carried out, so a future UI-automation path can sit behind the same kind of interface.

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
  and the track is matched from scratch. This also means a made-up mock ID could not
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
- **Live tests** (`tests/integration/`, marker `music_app`) drive the real app and run
  only with `pytest --music-app`. They change one playlist, `Spotify Daily Mix TEST`,
  including a full `sync` into it; they put its contents back afterwards, never delete
  it, and assert that the library and every other playlist are unchanged.

## Deliberately absent

No server, no web framework, no ORM, no async, no plugin system, no Apple API client
and no credential handling.
