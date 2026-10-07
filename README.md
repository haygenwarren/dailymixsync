# Daily Mix Sync

A personal, Mac-only tool that recreates Spotify Daily Mix playlists in Apple Music.
It works from **track metadata only** (title, artist, album, duration). No audio is
downloaded, copied or transferred.

```
source track → remembered match, if its song is still in the library
             → else search the local Music library                      (AppleScript)
             → else, with --catalog: search the Apple Music catalog     (Music window)
                    add the match to the library                        (Music window)
                    wait until the library shows it, then check it      (AppleScript)
             → remember the match → write the managed playlist          (AppleScript)
```

Apple Music is reached by driving the **Music app on this Mac**, which is already
signed in to your subscription. There is no Apple Developer Program membership, no
MusicKit, no API key or token, and no hosted service.

## Status

| Piece | State |
| --- | --- |
| Local-library matching, with cache and manual review | Working, tested against the real app |
| Apple Music catalog fallback for songs not in the library (`sync --catalog`) | Working, tested against the real app |
| Playlist sync: write, verify, roll back on failure | Working, tested against the real app |
| Spotify browser extractor | Not built. The export file has to come from elsewhere for now |

The Apple Music side is complete. What is missing is the Spotify side: something to
produce the export file.

## Requirements

- **macOS** with the **Music** app. Developed and tested on macOS 26.3 with Music 1.6.3.
- **Python 3.12** or newer.
- Music **signed in** to an Apple Music subscription, with Sync Library on, so that
  catalog songs can live in your library and in playlists.
- **Automation permission** for the app you run the tool from, and **Accessibility
  permission** as well if you use the catalog fallback; see [Permissions](#permissions).

## Setup

Requires Python 3.12 or newer. From the repository root:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Dependencies: [`rapidfuzz`](https://github.com/rapidfuzz/RapidFuzz) for fuzzy string
matching, and `pytest` for the tests. SQLite, JSON and logging come from the standard
library, and Music is driven with the `osascript` command that ships with macOS.

## Try it offline

This needs neither Music nor any of your data: it matches a sample playlist against
a made-up catalog.

```sh
python -m daily_mix_sync match samples/daily_mix_sample.json \
    --mock-catalog samples/mock_apple_catalog.json --details
```

`samples/daily_mix_sample.json` holds 11 tracks written the way Spotify formats them.
`samples/mock_apple_catalog.json` holds 21 catalog entries written the way Apple Music
formats them, with wrong answers (remixes, covers, live versions, same-titled songs by
other artists) listed ahead of the right ones. Expected result:

```
Daily Mix 1 (sample)
--------------------
Tracks found:       11
Cached matches:      0
New matches:         8
Needs review:        1
Failed:              2
```

Run it a second time and the 8 matches come from the cache instead of being searched
again. Delete `data/mock_mappings.sqlite3` to start over.

## Syncing a playlist

```sh
# See what would happen. Nothing in Music is created or changed.
python -m daily_mix_sync sync data/daily_mix_1.json --dry-run

# Do it. Asks about ambiguous songs, then asks before replacing the playlist.
python -m daily_mix_sync sync data/daily_mix_1.json

# Later, unattended: no questions.
python -m daily_mix_sync sync data/daily_mix_1.json --yes

# Also fetch songs you do not have yet from the Apple Music catalog.
python -m daily_mix_sync sync data/daily_mix_1.json --catalog
```

For a first try that cannot touch a playlist you care about, send the result to the
test playlist instead:

```sh
python -m daily_mix_sync sync data/daily_mix_1.json --into "Spotify Daily Mix TEST"
```

What `sync` does, in this order:

1. Reads the export and works out the destination playlist.
2. For each track, uses the remembered match if its song is still in the library;
   otherwise searches the library and scores the candidates.
3. Asks you about the ambiguous ones, if someone is at the keyboard.
4. With `--catalog`, looks in the Apple Music catalog for what is still missing and
   adds the matches to your library; see
   [Songs that are not in your library](#songs-that-are-not-in-your-library).
5. Prints the summary, including what could not be found.
6. Asks for confirmation (skipped with `--yes`).
7. Records what the playlist holds, empties it, adds the new songs in export order.
8. Reads the playlist back and compares it, song for song and in order.
9. If anything in steps 7 and 8 failed, puts the recorded contents back.

Everything that can go wrong with matching happens before step 7, so a problem there
never leaves a playlist half-written.

```
Daily Mix 1
-----------
Tracks found:        50
Cached matches:      23
New matches:         19
Manual matches:       2
Needs review:         0
Not in library:       6

Not in the Music library:
  Song A — Artist A
  Song B — Artist B  (closest: Song B (Live), score 70.0)
  ...
  The Apple Music catalog was not searched for these. `sync --catalog` looks for them there.

Destination:      Spotify Daily Mix 1  (48 track(s) now)
New contents:     44 of 50 track(s), in playlist order

Cleared old tracks: 48
Added new tracks:   44
Verified:           44 / 44, in order

✓ Playlist updated and verified.
```

| Option | Meaning |
| --- | --- |
| `--dry-run` | Match and report only. Creates, empties and adds nothing, and asks nothing. Matches found in the library are still remembered. With `--catalog` it searches the catalog and lists what it would add, without adding. |
| `--yes` | Do not ask before replacing the playlist's contents. |
| `--no-review` | Do not ask about ambiguous songs, in the library or the catalog; leave them out. |
| `--catalog` | Look in the Apple Music catalog for songs the library lacks and add the matches to your library. Off unless you ask for it (`--no-catalog` is the default). |
| `--into NAME` | Write to this managed playlist instead of the one named after the export. |
| `--db FILE` | Mapping database. Default: `database_path` from the settings. |
| `--details` | List every matched song and the track chosen for it. |

**Which playlist is written.** The managed prefix goes in front of the export's
`playlist_name`, without repeating what the two share: `Daily Mix 1` becomes
`Spotify Daily Mix 1`, and `Spotify Daily Mix 1` stays as it is. Any other name is
simply appended, so `Discover Weekly` becomes `Spotify Daily Mix Discover Weekly`.
The result always starts with the prefix, so nothing in an export can point the tool
at one of your own playlists, and `--into` is refused for any name that is not
managed.

**Order and duplicates.** Songs are written in export order. A song that cannot be
resolved is left out and the rest keep their relative order. Exact duplicate entries
in the export are dropped when it is read (and counted in the summary); two different
entries that turn out to be the same song in your library are both written.

**Confirmation.** Without `--yes`, `sync` asks before replacing anything. If there is
no one to ask (input is not a terminal), it stops without changing anything.

**When nothing matches.** If no track can be resolved, `sync` reports an error and
leaves the playlist alone. Unresolved tracks are otherwise not an error.

**If the write fails.** The previous contents are put back and the command reports
both the failure and the restore, with exit code 1. Music has no transactions, so this
is a best effort: if the restore fails too, the command says manual intervention is
needed and lists what the playlist held.

## Songs that are not in your library

AppleScript cannot search the Apple Music catalog, so for songs you do not have yet
the tool operates the Music window the way you would: it selects Search in the
sidebar, switches the scope to Apple Music, types the query, reads the Songs results,
and for the one it settles on opens the More menu and chooses **Add to Library**.
From there on it is back to AppleScript.

```sh
python -m daily_mix_sync sync data/daily_mix_1.json --catalog
```

For each track the library could not supply:

1. **Search.** The query is the base title plus the primary artist, the same one used
   for the library.
2. **Score.** The results are scored by the same matcher as library candidates. The
   results page shows title and artist only, so album and duration play no part yet.
3. **Choose.** A result at or above the accept threshold (90) is chosen. Results in
   the review band (75 to 90) are put to you, if someone is at the keyboard. Anything
   lower is not added.
4. **Add.** "Add to Library" is chosen for that one result. If Music shows the song as
   already in your library, nothing is added.
5. **Wait.** The library is searched about once a second, for up to `catalog_wait_s`
   (30) seconds, until the song shows up there.
6. **Check.** The library track now has an album and a duration, so it is scored
   against the export's track again, in full. Only if it still qualifies is it used
   and remembered, under its Music persistent ID, exactly like any other match.

On later runs that song is found in the library or the cache, and the window is not
used for it.

```
Searching the Apple Music catalog for 2 track(s). Music will come to the front; please
leave the Mac alone until it hands back.
Daily Mix 1
-----------
Tracks found:         5
Cached matches:       3
New matches:          0
From catalog:         1
Needs review:         0
Not in library:       1

Added to your library from the Apple Music catalog:
  Killing In The Name — Rage Against The Machine  →  Killing In The Name — Rage Against the Machine

Not in the Music library:
  A Song That Is Not There — Nobody At All
      catalog: no matching song in the catalog; closest was ..., score 33.6
```

What to know before using it:

- **It is opt-in.** Without `--catalog`, the window is never touched and songs you do
  not have are simply left out, as before.
- **It takes over the screen.** macOS only lets a program see the Music window while
  Music is in front on the visible desktop, so Music comes forward for the duration
  and the app you were in comes back afterwards. A search takes a few seconds per
  track. **Do not use the Mac while it runs**: the query is typed with real keystrokes.
  The tool checks that Music's search field has the keyboard immediately before it
  types and before it presses Return, and stops if it does not, but switching apps at
  that instant could still send keystrokes elsewhere.
- **It adds songs to your library and does not take them out.** Every song it adds
  stays, including one that then fails the check in step 6 and is left out of the
  playlist (the summary says so when that happens). Remove such a song by hand in
  Music if you do not want it.
- **It may pick a different release of the same recording.** With no album on the
  results page, the first result with the right title and artist wins, which is
  Apple's top hit and often a compilation rather than the original album.
- **A dry run adds nothing.** `sync --dry-run --catalog` searches the catalog and lists
  what it would add.
- **One failure does not sink the run.** A song that cannot be found, added or
  confirmed is reported with the reason and left out; the rest carry on. If the Music
  window itself is not laid out as expected, the catalog step stops, says so, and the
  sync continues with what it has.

The only things the window automation ever does are search, read results, and choose
"Add to Library". It never chooses another menu item, never deletes anything, and
never touches a playlist; playlists are written through AppleScript only.

## Reviewing ambiguous songs

A song is ambiguous when its best candidate scores between the review threshold (75)
and the accept threshold (90): close, but different in a way the matcher will not
wave through, such as an extra `(From "The Film")` on one side.

`sync` asks about these as it goes. To settle them on their own:

```sh
python -m daily_mix_sync review data/daily_mix_1.json
```

```
Needs review (1 of 1)

Source:
   Dancing Queen - From "Mamma Mia!"
   ABBA
   Arrival
   3:51

Candidates:

 1. Dancing Queen
    ABBA
    Arrival (Bonus Track Version)
    3:52
    Score: 80.0  (title 100, artist 100, album 100, duration 100; -20.0 qualifier mismatch: from mamma mia)

 2. Dancing Queen
    Colin Firth, Stellan Skarsgård, ...
    Score: 38.2  (...)

 s. Skip
 q. Quit review

Selection:
```

A number picks that candidate and stores it as a `manual` mapping: it is used on every
later run without asking, and an automatic match never replaces it. `s` or Enter
skips the song, which is then left out and asked about again next time. `q` stops
asking. Nothing is ever picked for you.

To be asked about weaker candidates as well, lower `review_threshold` in the settings.

## Other commands

Run everything from the repository root; relative paths such as `data/` are resolved
against the current directory.

```sh
python -m daily_mix_sync validate PLAYLIST.json
python -m daily_mix_sync match PLAYLIST.json [--mock-catalog CATALOG.json] [options]
```

**`validate`** parses a playlist export and lists its tracks with the cache key each
one will use. It reports duplicates and entries that are missing a title or artist.

**`match`** does the matching half of `sync` and nothing else: it searches your Music
library, remembers every automatic match, and prints the summary with full detail on
what needs review or was not found. It never changes anything in Music.

| Option | Meaning |
| --- | --- |
| `--mock-catalog FILE` | Search this JSON fixture instead of the Music library. `review` accepts it too. |
| `--db FILE` | Mapping database. Default: `database_path`, or `data/mock_mappings.sqlite3` with a mock catalog. |
| `--details` | Also list every matched track and the candidate chosen for it. |
| `--config FILE` | Settings file. Default: `./config.json` if present, else built-in defaults. |
| `-v` / `-vv` | Log each track's decision / also every candidate's score breakdown. |

Each track ends up in one of these states:

| State | Meaning |
| --- | --- |
| Cached | A remembered match whose song is still in the library; no search is made. |
| New match | Best candidate scored at or above `auto_accept_threshold` (90); remembered. |
| Manual match | You picked a candidate during review in this run; remembered as `manual`. |
| Needs review | Best candidate scored between `review_threshold` (75) and 90; nothing stored. |
| Not in library | No search results, or the best candidate scored below 75; nothing stored. With a mock catalog this row is called Failed. |

Problems with the input, the settings, the database or Music are reported as a
one-line `error: ...` on stderr with exit code 1.

### Music app commands

These exercise the Music integration on its own, without any Spotify data.

```sh
python -m daily_mix_sync music-test
python -m daily_mix_sync music-playlists
python -m daily_mix_sync music-find "Dancing Queen" "ABBA" [--album NAME] [--duration 3:52]
python -m daily_mix_sync music-add-test "Dancing Queen" "ABBA"
python -m daily_mix_sync music-clear-test [--delete]
```

| Command | What it does | Changes Music? |
| --- | --- | --- |
| `music-test` | Checks Music can be reached (starting it if needed) and prints its version, library size and managed playlists. Also says whether window control (Accessibility) is permitted. | No |
| `music-playlists` | Lists your playlists, marking smart ones, folders and managed ones. | No |
| `music-find` | Searches your library for one song and shows every candidate with its score. Exit code 0 only for a confident match. | No |
| `music-add-test` | Finds the song the same way and, only on a confident match, adds it to `Spotify Daily Mix TEST`, creating that playlist if needed. Then re-reads the playlist to confirm. | Only the TEST playlist |
| `music-clear-test` | Empties `Spotify Daily Mix TEST`, or deletes it with `--delete` (see the note on deleting under [What was verified](#what-was-verified)). | Only the TEST playlist |

`music-find "Nutshell" "Alice In Chains"` against a library holding both the studio and
the live recording prints, in short:

```
Library search 'nutshell alice in chains': 2 candidate(s)
  100.0  Nutshell — Alice In Chains [Jar of Flies - EP] 4:19  (id 1A2B3C4D5E6F7081)
   70.0  Nutshell (Live) — Alice In Chains [MTV Unplugged (Live)] 4:57  (id 9F8E7D6C5B4A3021)
         ... -30.0 version mismatch: live
Result: match (score 100.0; accepted from 90).
```

### Music window commands

These exercise the catalog automation on its own. All of them bring Music to the front
while they run and need Accessibility permission.

```sh
python -m daily_mix_sync music-ui-inspect [--dump]
python -m daily_mix_sync music-catalog-search "Dreams" "Fleetwood Mac" [--album NAME] [--duration 4:17]
python -m daily_mix_sync music-catalog-add-test "Song" "Artist" [--yes]
```

| Command | What it does | Changes Music? |
| --- | --- | --- |
| `music-ui-inspect` | Checks that the Music window has every part the automation relies on and lists them. `--dump` also lists every element of the toolbar and main pane. Exit code 1 if something is missing. | Selects Search in the sidebar; nothing else |
| `music-catalog-search` | Searches the Apple Music catalog for one song and prints the results with their scores, best first. Exit code 0 only if one would be chosen. | Leaves the search on screen; adds nothing |
| `music-catalog-add-test` | Does the same search, asks before adding (unless `--yes`), chooses Add to Library for the match, waits for it to show up, and prints its persistent ID. | **Adds one song to your library.** No playlist is touched |

`music-catalog-add-test` is for a song you deliberately pick as a test. There is no
command to take it out again; delete it in Music if you do not want to keep it.

## Playlist export format

This is what the browser extractor will produce. Until it exists, write the file by
hand or generate it some other way.

```json
{
  "playlist_name": "Daily Mix 1",
  "tracks": [
    {
      "title": "Bohemian Rhapsody - Remastered 2011",
      "artist": "Queen",
      "album": "A Night At The Opera (2011 Remaster)",
      "duration_ms": 354000,
      "spotify_track_id": "…",
      "spotify_url": "https://open.spotify.com/track/…"
    }
  ]
}
```

- `title` and `artist` are required. List every credited artist in `artist`, comma
  separated, as Spotify shows them.
- `album`, `duration_ms`, `spotify_track_id` and `spotify_url` are optional. A missing
  album or duration is left out of the score rather than counted against a candidate.
- If only `spotify_url` is given, the track ID is taken from it.
- If `playlist_name` is missing, the file name is used.
- Keep your own exports in `data/`, which is git-ignored.

## Configuration

Thresholds, weights and penalties are not hardcoded. To change them:

```sh
cp config.example.json config.json    # config.json is git-ignored
```

| Key | Default | Meaning |
| --- | --- | --- |
| `database_path` | `data/mappings.sqlite3` | Mapping database for runs against your Music library. |
| `search_limit` | 10 | Candidates requested per search. |
| `managed_playlist_prefix` | `Spotify Daily Mix` | The only playlists the tool may change; see [Playlist safety](#playlist-safety). |
| `catalog_wait_s` | 30 | How long to wait for a song added from the catalog to show up in the library. |
| `matching.auto_accept_threshold` | 90 | Accept automatically at or above this score. |
| `matching.review_threshold` | 75 | Offer for review at or above this score. |
| `matching.weight_title` / `_artist` / `_album` / `_duration` | 0.45 / 0.35 / 0.10 / 0.10 | Share of each similarity in the score. |
| `matching.duration_tolerance_s` | 3 | Duration differences up to this count as identical. |
| `matching.duration_zero_s` | 15 | Duration similarity reaches 0 at this difference. |
| `matching.duration_mismatch_s` / `_penalty` | 30 / 15 | From this difference on, subtract the penalty as well. |
| `matching.qualifier_mismatch_penalty` | 20 | Unrecognised qualifiers differ, e.g. `(Taylor's Version)` on one side only. |
| `matching.flag_penalties` | live, remix, acoustic, demo, instrumental: 30; radio_edit: 15; remaster: 2 | Version marker present on one side only. |

Unknown keys are rejected, so a typo cannot silently do nothing. How the score is put
together is described in [ARCHITECTURE.md](ARCHITECTURE.md).

## Mapping cache

Matches are stored in SQLite, one row per source track:

| Column | Content |
| --- | --- |
| `source_key` | `spotify:track:<id>`, or `meta:<artists>\|<title>\|<album>\|<seconds>` built from normalized metadata when there is no Spotify ID |
| `music_persistent_id` | Music's persistent ID for the matched track: the stable identifier the app exposes, the same for a song in the library and in any playlist |
| `score` | Match score when the mapping was stored |
| `method` | `auto` or `manual` |
| `matched_at` | When this pairing was first stored (UTC) |
| `source_title`, `source_artist`, `apple_title`, `apple_artist`, `apple_album` | Not used by the program; there so the table is readable |

A `manual` mapping is never overwritten by an `auto` one.

A remembered match is only trusted while its song is still in the library. Each run
looks every remembered song up again; one that has gone is reported, removed from the
cache, and matched from scratch. The summary then shows a `Stale mappings` line.

To look at the cache:

```sh
sqlite3 -header -column data/mock_mappings.sqlite3 \
  "SELECT source_key, music_persistent_id, score, method, apple_title FROM mappings"
```

Mock runs write to `data/mock_mappings.sqlite3`, never to the real cache, because mock
track IDs are made up and must not end up in a real playlist. Pointing a mock run at
the real database with `--db` is refused. A database written
before the identifier column was renamed (it used to be `apple_catalog_id`) is
upgraded in place the first time it is opened.

## Tests

There are three kinds, kept apart.

**Unit tests** need nothing but Python and never touch Music:

```sh
python -m pytest
```

600 tests: normalization, scoring, the SQLite store, input validation, config, the
mock catalog, cache validation, manual review, playlist writing with verification and
rollback, catalog resolution, the CLI, the Music adapter and the Music-window module.
Everything that would talk to Music runs against in-memory stand-ins: one for
`osascript` (`tests/fake_music.py`), which can be told to fail at a chosen point, and
one for the Music window (`tests/fake_ui.py`). Tests also read the window scripts
themselves to check what they are allowed to do: that Add to Library is the only menu
item ever chosen, and that nothing is typed without the keyboard check. As a backstop,
a unit test that tries to start any real program fails on the spot.

**Live tests of the Music app** drive it through AppleScript and are skipped unless
you ask for them:

```sh
python -m pytest tests/integration --music-app
```

20 tests, under a minute. They read your library and change exactly one playlist,
`Spotify Daily Mix TEST`, including a complete `sync` into it from a generated
export. Afterwards its contents are put back exactly as they were found, and the
tests check that your library size and every other playlist are unchanged. They use a
temporary mapping database, so your real cache is not touched. They never delete the
test playlist; if they had to create it, it is left in place, empty. They need the
Automation permission below.

**Live tests of the Music window** operate it through Accessibility, and have their
own switch:

```sh
python -m pytest tests/integration --music-ui
```

6 tests, under a minute, with Music in front the whole time; leave the Mac alone while
they run. They change nothing: they check the window's layout, search the catalog,
read results and menus, and try "add" only on a song Music already shows as being in
your library, where it must do nothing. Adding a song you do not have is not
automated as a test, because it cannot be undone automatically; use
`music-catalog-add-test` for that. They need both permissions below.

## How Spotify extraction will work

Spotify's Web API does not return the contents of Spotify-owned personalised playlists
such as Daily Mixes, so the track list has to come from the Spotify web player itself.

The plan is a minimal Chrome Manifest V3 extension for `open.spotify.com`.
You open a Daily Mix, click the extension, and it reads the playlist title and the
track rows (title, artists, album, duration, track link) and saves them as a JSON file
in the format above. The track list is virtualised, so the script scrolls the list and
collects rows as they appear, keyed by track link, rather than assuming a fixed count.
Spotify-specific DOM selectors will be kept in one place.

## Music app integration

Everything Music-specific is in two modules. `music_app.py` runs AppleScript through
`osascript` and does all the reading and all the playlist writing. `music_ui.py`
operates the Music window through Accessibility and does only what AppleScript cannot:
searching the Apple Music catalog and adding a song from it.

Each AppleScript command used was read from the scripting dictionary of the installed
app (`Music.app/Contents/Resources/com.apple.Music.sdef`) and then tried against the
real app; each part of the window that is relied on was read from the installed app's
accessibility tree. Nothing was taken from old iTunes examples or tutorials.

### No Apple credentials

The tool talks to the Music app, and the Music app is signed in as you. Nothing here
needs or stores an Apple Developer account, a MusicKit key, an Apple Music API token
or any other credential, and there are no environment variables to set.

### What was verified

On macOS 26.3.1 with Music 1.6.3, through the adapter:

| Capability | Result |
| --- | --- |
| Reach Music from Python and read its version, library size and playlists | Works |
| Find a library song by title and artist, scored by the existing matcher | Works |
| Create a playlist, or reuse it if it exists | Works; never creates a second one with the same name |
| Add library songs to a playlist | Works, in the order given; the same song can be added twice |
| Remove one song from a playlist | Works; the song stays in the library |
| Empty a playlist | Works; the playlist keeps its identity (same persistent ID) |
| Delete a playlist and create it again | Works locally, with a catch: see below |
| Match a whole export against the library, with cache and stale-entry handling | Works |
| Review an ambiguous song and have the choice remembered | Works |
| Replace a playlist's contents in export order, then read back and compare | Works; the playlist keeps its identity |
| Search the Apple Music catalog, or add a song that is not in the library | **Not possible through AppleScript.** The dictionary has no such command; done through the Music window instead |
| Search the catalog through the Music window and read title and artist of the song results | Works |
| Add one catalog song to the library through its More menu, then find it through AppleScript | Works; the song was visible to AppleScript within about ten seconds, with its own persistent ID |
| Repeat that for a song already in the library | Nothing is added; Music's menu shows it as present |
| `sync --catalog`: library songs, one catalog song and one nonexistent song in a single run | Works; the playlist was written in export order and verified, the missing song left out |

A song has the same persistent ID in the library and in every playlist it is in, which
is what makes that ID usable as the cached mapping.

**Updating a playlist** is done by emptying the managed playlist and adding the new
songs in order. That keeps the playlist's identity, so Music goes on treating it as
the same playlist rather than a new one.

**Deleting and recreating is avoided.** During testing, a playlist that was deleted
and created again under the same name a moment later came back a few minutes
afterwards as a *second* playlist with that name, holding what it had last synced.
Nothing was running at the time, so this was iCloud's Sync Library restoring the
deleted one. `sync` never deletes a playlist, and neither do the live tests. If you
ever see two playlists with the same managed name, the tool stops and says so rather
than pick one; delete the extra in Music.

**Reading a playlist in its true order.** Music reports a playlist's tracks in the
order it is displayed in, so a playlist sorted by a column would read back in a
different order from the one its songs were added in. The tool switches Music's
`fixed indexing` scripting flag on while it reads a playlist and puts it back
afterwards, which gives the playlist's own order. This is what makes the order check
after a sync meaningful.

Not exercised, because doing so would have meant disturbing your setup: starting
Music from cold (it was already running) and the path where Automation permission is
denied (it was already granted). The code for both exists and is unit tested.

### How library search behaves

Music's `search` command, as observed:

- Every word must match, as the beginning of a word, somewhere in the song's details:
  title, artist, album, and other fields such as composer. Word order does not matter.
- Case, accents and punctuation are ignored: `beyonce` finds Beyoncé, `acdc` and
  `ac dc` both find AC/DC, `aint` finds "Ain't".
- One word that is not there means no result. The tool therefore searches for the
  base title plus the primary artist only, and drops the word "and", since the library
  entry may say "&".
- An empty search is an error in Music; the tool does not send one.
- The library can hold music videos; only songs are returned.

Search only narrows the field. The existing matcher still scores every candidate, so
a live version, a remix or a same-titled song by someone else is not accepted just
because the search returned it.

### Playlist safety

The tool changes only **managed playlists**: those named exactly the managed prefix
(default `Spotify Daily Mix`), or the prefix followed by a space and more, such as
`Spotify Daily Mix 1`. `Spotify Daily Mixtape` and `Spotify Liked Songs` are not
managed. Names are compared exactly, including capitalisation.

- Creating, adding to, removing from, emptying and deleting are all refused for any
  other name, before anything is sent to Music.
- The AppleScript that makes the change checks again: it looks the playlist up by its
  persistent ID and refuses unless it still has the expected name, that name is
  managed, and it is an ordinary playlist (not a folder, not a smart playlist, not one
  of Music's own).
- If two playlists have the same managed name, the tool stops instead of picking one.
- Songs are only ever removed from a playlist, never from the library.

The prefix is the whole boundary. If you change `managed_playlist_prefix`, pick
something no playlist of yours already starts with: a prefix of `Spotify Liked` would
make an existing "Spotify Liked Songs" playlist fair game.

The `music-*` commands and the live tests only ever use `Spotify Daily Mix TEST`.
`sync` writes to the managed playlist named after the export, or the one given with
`--into`.

## Permissions

macOS asks before one app may control another. The permission belongs to the app you
run the tool **from**: Terminal, iTerm, Visual Studio Code, and so on.

### Automation (needed now)

The first time the tool talks to Music, macOS shows a prompt along the lines of
"Terminal wants access to control Music". Choose **Allow**.

To check or change it later:

1. Open **System Settings → Privacy & Security → Automation**.
2. Find the app you run the tool from.
3. Switch on **Music** underneath it.

If the permission is missing, commands stop with:

```
error: macOS has not allowed this program to control Music. Open System Settings →
Privacy & Security → Automation, ...
```

If the app is not listed at all, the prompt was never answered. Running any `music-*`
command again should bring it back. If a command instead waits and then reports that
Music did not answer, look for a permission prompt hidden behind another window.

### Accessibility (needed only for the catalog fallback)

Anything that operates the Music window needs a second permission: `sync --catalog`,
`music-ui-inspect`, `music-catalog-search` and `music-catalog-add-test`. Everything
else works without it.

1. Open **System Settings → Privacy & Security → Accessibility**.
2. Add the app you run the tool from, and switch it on.

This is a broad permission: it lets that app operate any window on your Mac, not only
Music. Grant it to the terminal you actually run the tool from, and switch it off
again if you stop using the catalog fallback.

`music-test` reports the current state on its last line, either
`Window control: permitted` or:

```
Window control: not permitted. Only needed for songs that are not in your library:
                System Settings → Privacy & Security → Accessibility
```

Without it, the commands above stop before doing anything with:

```
error: Searching the Apple Music catalog needs macOS Accessibility permission. macOS
has not allowed this program to operate other apps' windows. Open System Settings →
Privacy & Security → Accessibility, ...
```

The tool checks once and stops; it does not keep trying.

## After a macOS or Music update

The catalog fallback depends on how the Music window is built, and that can change
with an update. Everything it knows about the window is in one file,
`src/daily_mix_sync/music_ui.py`, and `ARCHITECTURE.md` records the layout it was
written against (Music 1.6.3, macOS 26.3).

If catalog search stops working:

1. Run `python -m daily_mix_sync music-ui-inspect`. It lists each part the automation
   needs and marks anything it cannot find as `MISSING`.
2. Run it again with `--dump` to see every element that is there now, under the names
   System Events gives them.
3. Compare with the layout table in `ARCHITECTURE.md` and adjust the matching handler
   at the top of `music_ui.py`. Nothing outside that file knows about the window.
4. Run `python -m pytest tests/integration --music-ui` to confirm.

Until then, `sync` without `--catalog` is unaffected: it does not use the window.

## Known limitations

- There is no Spotify extractor yet, so the export file has to be written by hand or
  produced some other way.
- **The catalog fallback is the fragile part.** It depends on the layout of the Music
  window, which an update can change; see
  [After a macOS or Music update](#after-a-macos-or-music-update). It also:
  - assumes Music is in English: it looks for the sidebar entry "Search", the scope
    "Apple Music", the section "Songs" and the menu item "Add to Library" by name;
  - needs Music in front and the Mac left alone while it runs;
  - reads only the first screen of song results (nine in testing), not "see all";
  - sees no album or duration before adding, so it may add a different release of the
    right recording, and occasionally one that then fails the full check;
  - types the query in plain ASCII (accents and punctuation are stripped first), so
    titles in other scripts cannot be searched for;
  - cannot take back a song it has added.
- A song can be in your library and still not be found if Spotify and Apple Music
  credit a different primary artist: the library search needs every word of the title
  and of the first artist to be present.
- A song you skip in review is not remembered as skipped; you are asked again on the
  next run. `--no-review` avoids the questions.
- A dry run still remembers the matches it finds in the library. It changes nothing
  in Music.
- The restore after a failed write is a best effort, not a transaction. It was tested
  with simulated failures, not by breaking the real Music app mid-write.
- Scoring was tuned on hand-written fixtures and spot-checked against a real library
  and real catalog results. Expect to adjust thresholds and penalties with wider use.
- Explicit and clean versions of a track are not told apart.
- A live recording is recognised from its title (`(Live)`, `- Live at …`), not from
  the album name alone.
- Part numbers in the title proper (`…, Pt. 1` vs `…, Pt. 2`) are only separated by
  duration. Inside brackets (`(Pt. 1)`) they are compared.
- Titles that are translated or transliterated differently on the two services will
  not match.
- For tracks without a Spotify ID, the cache key depends on the normalization rules.
  If those rules change, such a track is simply matched again.

## Next steps

1. **Spotify extractor.** A Chrome extension that reads an open Daily Mix in the
   Spotify web player and saves it in the export format above, so the file no longer
   has to be made by hand. This is the one piece left.
