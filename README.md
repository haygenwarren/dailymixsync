# Daily Mix Sync

A personal, Mac-only tool that recreates Spotify Daily Mix playlists in Apple Music,
**using only songs that are already in your Apple Music library**. It works from
track metadata (title, artist, album, duration). No audio is downloaded, copied or
transferred.

```
Spotify Daily Mix, open in the Spotify web player
→ browser extension: the track list, saved as a JSON file
→ matching against your local Apple Music library
→ a Daily Mix playlist made of the songs you already have
```

Songs in a Daily Mix that are not in your library are **left out on purpose**. Nothing
is ever added to your library by a sync. That is the design, for four reasons:

- it keeps your Apple Music library clean;
- it avoids adding unfamiliar songs automatically;
- it makes the sync path much simpler and more reliable;
- it needs no UI automation, only AppleScript.

Apple Music is reached by driving the **Music app on this Mac**, which is already
signed in to your subscription. There is no Apple Developer Program membership, no
MusicKit, no API key or token, and no hosted service.

## Status

| Piece | State |
| --- | --- |
| Matching against your Music library, with cache and manual review | Working, tested against the real app |
| Library-only playlist sync: write, verify, roll back on failure | Working, tested against the real app |
| Spotify export, a Chrome extension in [`extension/`](extension/README.md) | Working. Tested in a real Chrome against the real site on public playlists, and used on one real Daily Mix; see [what was verified](#what-was-verified-against-spotify) |
| Adding songs you do not have, from the Apple Music catalog | Not part of sync. Kept as [experimental commands](#experimental-apple-music-catalog-support) only |

## Requirements

- **macOS** with the **Music** app. Developed and tested on macOS 26.3 with Music 1.6.3.
- **Python 3.12** or newer.
- Music **signed in** to an Apple Music subscription, with Sync Library on, so that
  catalog songs can live in your library and in playlists.
- **Automation permission** for the app you run the tool from; see
  [Permissions](#permissions). Nothing else: a sync needs no Accessibility permission.
- **Google Chrome**, or another Chromium browser, for the Spotify export extension.
- **Node.js** only if you want to run the extension's tests. The extension itself has
  no dependencies and no build step.

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

## From Spotify to Apple Music

The whole path, once the extension is loaded
([how to load it](extension/README.md#install): `chrome://extensions` → Developer
mode → Load unpacked → the `extension/` folder).

1. **Open a Daily Mix** in the Spotify web player, <https://open.spotify.com>.
2. **Export it.** Click the extension's button, then **Export this playlist**. It
   reads every track and downloads a file named after the playlist, such as
   `daily_mix_1.json`.
3. **Move the file into `data/`**, which is git-ignored:

   ```sh
   mv ~/Downloads/daily_mix_1.json data/
   ```

4. **Validate it.** This reads the file and touches nothing else:

   ```sh
   python -m daily_mix_sync validate data/daily_mix_1.json
   ```

5. **Dry run.** See which songs are in your library and what the playlist would hold.
   Nothing in Music is created or changed:

   ```sh
   python -m daily_mix_sync sync data/daily_mix_1.json --dry-run
   ```

6. **Sync.**

   ```sh
   python -m daily_mix_sync sync data/daily_mix_1.json
   ```

The export is used as it comes out of the extension; there is nothing to edit by hand.
The sync stays **library-only**: the playlist `Spotify Daily Mix 1` gets the songs of
the mix that are already in your Apple Music library, and the rest are listed and left
out.

**All your mixes at once.** Export each Daily Mix (steps 1 to 3), then give them all to
one command:

```sh
python -m daily_mix_sync sync data/daily_mix_*.json --dry-run
python -m daily_mix_sync sync data/daily_mix_*.json
```

Each export goes to its own playlist, everything is matched before any playlist is
changed, and you are asked once for the whole lot. See
[Several mixes at once](#several-mixes-at-once).

The extension only reads the Spotify page. It does not start a sync, run Python, or
talk to Music; the two halves meet in the JSON file and nowhere else.

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

# Several mixes in one go. See "Several mixes at once" below.
python -m daily_mix_sync sync data/daily_mix_*.json
```

For a first try that cannot touch a playlist you care about, send the result to the
test playlist instead:

```sh
python -m daily_mix_sync sync data/daily_mix_1.json --into "Spotify Daily Mix TEST"
```

What `sync` does, in this order:

1. Reads the export and works out the destination playlist.
2. For each track, uses the remembered match if its song is still in your library.
3. Otherwise searches your library and scores the candidates. A confident match is
   accepted and remembered.
4. Asks you about the ambiguous ones, if someone is at the keyboard.
5. Leaves out every track that is not in your library, and lists them.
6. Asks for confirmation (skipped with `--yes`).
7. Records what the playlist holds, empties it, adds the matched songs in Spotify order.
8. Reads the playlist back and compares it, song for song and in order.
9. If anything in steps 7 and 8 failed, puts the recorded contents back.

It never adds a song to your library, never searches the Apple Music catalog, and
never operates the Music window. Everything that can go wrong with matching happens
before step 7, so a problem there never leaves a playlist half-written.

```
Daily Mix 1
-----------
Tracks in Spotify export:  50
Cached library matches:    18
New library matches:       15
Manual matches:             3
Not in library:            14

Not in your Music library, so left out:
  - Song A — Artist A
  - Song B — Artist B  (closest: Song B (Live), score 70.0)
  ...
  Only songs already in your library are used. Nothing is added to it.

Destination:
Spotify Daily Mix 1

Previous tracks:           35
New tracks:                36

✓ Playlist updated and verified.
```

A track that is not in your library is not an error: the command still succeeds and
the playlist simply has fewer songs than the Daily Mix. "Closest" appears when your
library has the song only in another version, such as a live recording, which is not
accepted as a match.

| Option | Meaning |
| --- | --- |
| `--dry-run` | Match and report only. Creates, empties and adds nothing, and asks nothing. Matches found are still remembered. |
| `--yes` | Do not ask before replacing the playlist's contents. With several exports, skips the one question for the batch. |
| `--no-review` | Do not ask about ambiguous songs; leave them out. Applies to every export given. |
| `--into NAME` | Write to this managed playlist instead of the one named after the export. Only with a single export. |
| `--db FILE` | Mapping database. Default: `database_path` from the settings. |
| `--details` | List every matched song and the track chosen for it. |

**Which playlist is written.** The managed prefix goes in front of the export's
`playlist_name`, without repeating what the two share: `Daily Mix 1` becomes
`Spotify Daily Mix 1`, and `Spotify Daily Mix 1` stays as it is. Any other name is
simply appended, so `Discover Weekly` becomes `Spotify Daily Mix Discover Weekly`.
The result always starts with the prefix, so nothing in an export can point the tool
at one of your own playlists, and `--into` is refused for any name that is not
managed.

**Order and duplicates.** Songs are written in Spotify order. A song that is left out
does not disturb the order of the rest. Exact duplicate entries in the export are
dropped when it is read (and counted in the summary); two different entries that turn
out to be the same song in your library are both written.

**Confirmation.** Without `--yes`, `sync` asks before replacing anything. If there is
no one to ask (input is not a terminal), it stops without changing anything.

**When nothing matches.** If not one track is in your library, `sync` reports an error
and leaves the playlist alone rather than emptying it.

**If the write fails.** The previous contents are put back and the command reports
both the failure and the restore, with exit code 1. Music has no transactions, so this
is a best effort: if the restore fails too, the command says manual intervention is
needed and lists what the playlist held.

### Several mixes at once

`sync` takes any number of exports. Each one goes to the playlist named after it:

```sh
python -m daily_mix_sync sync \
    data/daily_mix_1.json \
    data/daily_mix_2.json \
    data/daily_mix_3.json \
    data/daily_mix_4.json
```

```
Daily Mix 1 → Spotify Daily Mix 1
Daily Mix 2 → Spotify Daily Mix 2
Daily Mix 3 → Spotify Daily Mix 3
Daily Mix 4 → Spotify Daily Mix 4
```

In practice you write `data/daily_mix_*.json`. That is your shell at work, not the
tool: before the command starts, the shell replaces the pattern with the names of the
files that match it, so the tool receives the same list as above. If no file matches,
zsh stops with "no matches found" and the tool is never run.

With one export, `sync` behaves exactly as described above. With several, the same
steps happen in an order that keeps every playlist safe:

1. **Everything is read and checked first.** Every export is loaded and every
   destination worked out. A missing file, a file that is not valid JSON, an export
   with no usable track, or two exports that would land in the same playlist stops the
   run here, with nothing in Music changed.
2. **Everything is matched.** All tracks of all exports, against one mapping database,
   so a song that is in several mixes is looked for once.
3. **Review, mix by mix.** Each question says which mix it belongs to. A track is asked
   about once even if it is in several mixes, and your choice is used in all of them.
   Quitting ends the questions for every mix; the run then carries on with what is
   settled.
4. **One report per mix**, in the order you gave the files, then the plan:

   ```
   Ready to update:

   Spotify Daily Mix 1    37 tracks  (currently 34)
   Spotify Daily Mix 2    42 tracks  (currently 40)
   Spotify Daily Mix 3    31 tracks  (new playlist)
   Spotify Daily Mix 4    16 tracks  (new playlist)

   Continue? [y/N]
   ```

5. **One question for all of it.** Anything but yes changes nothing. `--yes` skips the
   question; there is never one per playlist.
6. **The playlists are written one at a time**, each with its own record of what it
   held, its own check afterwards, and its own restore if something goes wrong.
7. **A summary** of the whole run:

   ```
   Batch sync complete

   Playlist     Spotify  In library   Result
   -------------------------------------------------------
   Daily Mix 1       50          37   ✓ updated
   Daily Mix 2       50          42   ✓ updated
   Daily Mix 3       50          31   ✓ created
   Daily Mix 4       50          16   ✓ created

   Total Spotify tracks:     200
   Already in library:       126
   Intentionally left out:    74

   4 playlists updated successfully.
   ```

**If one playlist fails to write.** That playlist is put back as it was, the failure is
reported, and the remaining playlists are still written. Playlists already finished are
not undone: each one is complete and verified on its own, and undoing a good write
would only add risk. The summary marks the one that failed, and the exit code is 1:

```
Daily Mix 3       50          31   ✗ write failed; previous contents restored
```

Songs that are not in your library are never counted as failures. "Intentionally left
out" is about songs; a ✗ is about a playlist.

**If you press Ctrl-C while it is writing.** The playlist in hand is put back, the rest
are not started, the summary is still shown, and the exit code is 130.

**A mix with nothing in your library.** Its playlist is left exactly as it is, not
emptied, and the other mixes are synced as usual. It is listed under "Left unchanged"
in the plan and in the summary. Only when not one mix has a song in your library is
the run an error.

**`--into` and several exports.** `--into` names one playlist, so it is refused when
more than one export is given. Sync that file on its own to send it somewhere else.

**Two exports, one playlist.** If two of the files would be written to the same
playlist, for instance an old and a new export of the same Daily Mix, the run is
refused and both files are named. The later one never silently overwrites the earlier.

| Exit code | Meaning |
| --- | --- |
| 0 | Every playlist that had songs to write was written and verified. |
| 1 | A problem before writing, not confirmed, no mix had any song in the library, or at least one playlist failed to write. |
| 130 | Interrupted. |

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

Review only ever offers songs that are already in your library. If none of them is
right, skip: the track is left out. There is no catalog search here.

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
| Not in library | No search results, or the best candidate scored below 75. The track is left out and nothing is stored. With a mock catalog this row is called Failed. |

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
| `music-test` | Checks Music can be reached (starting it if needed) and prints its version, library size and managed playlists. | No |
| `music-playlists` | Lists your playlists, marking smart ones, folders and managed ones. | No |
| `music-find` | Searches your library for one song the way `sync` does and shows the ten best candidates with their scores, saying which search found each. Exit code 0 only for a confident match. | No |
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

## Playlist export format

This is what the [browser extension](extension/README.md) produces. A file written by
hand or by anything else works the same, as long as it has this shape.

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
- Other fields are ignored. The extension adds two at the top level for your own
  reference, `source_url` and `exported_at`.
- Keep your own exports in `data/`, which is git-ignored.

## Configuration

Thresholds, weights and penalties are not hardcoded. To change them:

```sh
cp config.example.json config.json    # config.json is git-ignored
```

| Key | Default | Meaning |
| --- | --- | --- |
| `database_path` | `data/mappings.sqlite3` | Mapping database for runs against your Music library. |
| `search_limit` | 60 | How many results are taken from the first library search, by title and artist. |
| `title_search_limit` | 25 | How many are taken from the second search, by title alone, made only when the first gives no match. `0` turns it off. See [How library search behaves](#how-library-search-behaves). |
| `managed_playlist_prefix` | `Spotify Daily Mix` | The only playlists the tool may change; see [Playlist safety](#playlist-safety). |
| `catalog_wait_s` | 30 | Experimental catalog commands only: how long to wait for an added song to show up in the library. Not used by `sync`. |
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
cache, and matched from scratch against the library as it is now. If nothing suitable
is there any more, the track is left out like any other missing song. The summary
then shows a `Stale mappings` line.

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

**Unit tests** need nothing but Python and never touch Music:

```sh
python -m pytest
```

767 tests: normalization, scoring, the SQLite store, input validation, config, the
mock catalog, cache validation, manual review, playlist writing with verification and
rollback, the CLI, syncing several exports in one run, the Music adapter, the two-step
library search, and reading the extension's export. Everything that would talk to Music runs against an in-memory
stand-in for `osascript` (`tests/fake_music.py`), which can be told to fail at a
chosen point and which, like Music, answers a search in library order. One file,
`tests/test_search_recall.py`, covers how candidates are found and, above all, that
looking at more of them accepts nothing new: a duet is not matched to the solo
recording, nor a live, remixed, acoustic, demo or instrumental version to the
original, nor a same-titled song to another artist's. Another,
`tests/test_library_only.py`, pins down the
supported workflow itself: that nothing on the sync path imports the experimental
code, that `sync` has no option to add songs, that the supported commands never reach
the Music window or ask about Accessibility, and that a missing song is left out
rather than reported as a failure. As a backstop, a unit test that tries to start any
real program fails on the spot.

**Live tests of the library-only workflow** drive the real Music app through
AppleScript and are skipped unless you ask for them:

```sh
python -m pytest tests/integration --music-app
```

33 tests, about two minutes. Twenty of them read your library and change exactly one
playlist, `Spotify Daily Mix TEST`, including a complete `sync` into it from a
generated export. Seven more sync two exports in one run, into
`Spotify Daily Mix TEST 1` and `Spotify Daily Mix TEST 2` and nowhere else. Afterwards
the contents of all three are put back exactly as they were found, and the tests check
that your library size and every other playlist are unchanged. They use a temporary
mapping database, so your real cache is not touched. They never delete a test
playlist; one they had to create is left in place, empty. Your real
`Spotify Daily Mix 1`, `2` and so on are never used by a test. They need only the
Automation permission below.

The other six, in `tests/integration/test_search_live.py`, only search the library and
change nothing at all, not even the test playlist. To run just those:

```sh
python -m pytest tests/integration/test_search_live.py --music-app
```

The experimental catalog code has its own unit tests, included in the 767, and its
own live switch; see [the experimental section](#experimental-apple-music-catalog-support).

**The browser extension** has its own tests, in JavaScript, run with Node:

```sh
npm install     # once; installs jsdom, used only by these tests
npm test
```

141 tests, no browser needed: the pure functions, row reading against a fixture page
written the way Spotify writes it, the scroll loop against a simulated page that holds
only the rows near the viewport, and the extension's permissions and read-only rules.
The two suites share one file, `tests/extension/fixtures/expected_export.json`: the
JavaScript tests require the extension to produce it byte for byte, and
`tests/test_extension_export.py` loads it through the real importer.

```sh
npm run test:live
```

runs the real extension in a hidden Chrome with a throwaway profile, signed out,
against public playlists on the real Spotify site, and compares each export with what
the page reports. Details in the [extension README](extension/README.md#tests).

## How Spotify extraction works

Spotify's Web API does not return the contents of Spotify-owned personalised playlists
such as Daily Mixes, so the track list is read from the Spotify web player itself, by
a minimal Chrome Manifest V3 extension in [`extension/`](extension/README.md). There
is no Spotify login in this project, no OAuth, and no call to any Spotify API.

- **What it reads.** The playlist name, and for every row the title, the artists, the
  album, the duration and the track link, as the page shows them. Text is passed on
  untouched; normalization happens in Python.
- **The list is virtualised.** Spotify keeps only the rows near the viewport in the
  page. The extension scrolls the list in overlapping steps and stores every row under
  its position in the playlist, so a row seen many times is stored once and a gap
  cannot go unnoticed. It stops when every position up to the count the page states
  has been read, then puts the scroll position back. Nothing assumes fifty tracks.
- **It fails rather than exporting short.** A row that never appears, a playlist that
  changes mid-read, a missing name or a page it does not recognise all end in a
  message that says what was looked for, never in a quietly incomplete file.
- **Read-only.** The code in the Spotify tab only reads and scrolls. It never clicks
  or types, makes no network requests, and asks for no standing access to Spotify:
  its two permissions, `activeTab` and `scripting`, take effect only when you click
  its button. The tests enforce this by reading the source.
- **One place for Spotify's markup.** Every selector is in `extension/spotify_dom.js`,
  listed in the [extension README](extension/README.md#when-spotify-changes-its-page).

### What was verified against Spotify

On 2026-10-07, with Chrome 154, the real extension was loaded into a hidden Chrome
with a throwaway profile and run, signed out, against public playlists on
`open.spotify.com`:

| Playlist | Page says | Exported | Notes |
| --- | --- | --- | --- |
| Today's Top Hits | 50 songs | 50 | 11 tracks with several artists |
| Rock Classics | 200 songs | 200 | needs scrolling; also started from the bottom of the page |
| Viva Latino | 50 songs | 50 | 31 tracks with several artists; accents; a 520 × 430 window |
| K-Pop ON! (온) | 51 songs | 51 | Hangul in the playlist name and albums; a 700-wide window |

For each: the count equals the page's own, the first 30 tracks equal the list in the
page's metadata in the same order, no track is repeated, every track has title,
artist, album, duration and ID, and the page ends up scrolled where it started. The
popup's other states were checked too: not Spotify, not a playlist, and a failure on a
playlist address that does not exist.

All four files passed `validate` exactly as downloaded, and two went through
`sync --dry-run` against a real library (the 200-song list: 62 in the library, 5 for
review, 133 not in the library).

**A real Daily Mix.** Daily Mixes need a signed-in account, which that hidden browser
does not have, so this part was done by hand in an ordinary signed-in Chrome on
2026-10-07: one Daily Mix, 50 tracks exported, every one with album, duration and
track ID. The file passed `validate` unchanged with all 50 usable and went through
`sync --dry-run`. The other checks in the
[extension README](extension/README.md#trying-it-on-your-own-daily-mixes) (a second
mix, exporting from mid-scroll, a long playlist) have not been reported yet.

## Music app integration

Everything a sync says to Music is in one module, `music_app.py`, which runs
AppleScript through `osascript`. Each command it uses was read from the scripting
dictionary of the installed app (`Music.app/Contents/Resources/com.apple.Music.sdef`)
and then tried against the real app. Nothing was taken from old iTunes examples.

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
| Search the Apple Music catalog, or add a song that is not in the library | **Not possible through AppleScript.** The dictionary has no such command. Sync does not need it |
| A complete library-only `sync`: matched songs written in Spotify order, missing ones left out, library size unchanged | Works |

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
- **Results come in the library's own order, not best first.** A search for an
  album's title track brings back every song on the album, and the song itself can be
  anywhere among them.
- The search can be limited to song titles, which leaves album and artist names out.
- A search can return an entry that is not a readable track. The tool skips it.
- An empty search is an error in Music; the tool does not send one.
- The library can hold music videos; only songs are returned.

#### Two searches, one matcher

For each track that is not already in the cache, the tool looks in up to two ways:

1. **Title and primary artist**, anywhere in a song's details. The first 60 songs are
   taken (`search_limit`).
2. **Title alone, in song titles only**, and only if the first search gave no
   automatic match. The first 25 are taken (`title_search_limit`). This reaches a song
   whose artist is credited differently in the library, since no artist word is asked
   for, and a title track buried behind its own album, since album names no longer
   count.

A song found both ways is scored once. On equal scores, what the first search found
comes first.

**A wider search is not a looser match.** Both searches only decide which songs are
looked at. Every one of them is then scored by the same matcher against the same
thresholds, so a live version, a remix, a duet, or a same-titled song by someone else
is not accepted just because a search returned it. Nothing about scoring changed when
the second search was added.

A track that is not in the library costs two quick searches. Nothing walks through the
whole library.

#### What the depths are based on

The numbers come from an audit of a real library on 2026-10-07. Each of its 4,087
songs was searched for with the query the tool would build from that song's own title
and artist:

| Search | Songs found | Missed |
| --- | --- | --- |
| Title and artist, first 10 (how it used to work) | 4,041 | 46 |
| Title and artist, first 60 | 4,085 | 2 |
| Title and artist, first 10, then title alone, first 25 | 4,081 | 6 |
| **Title and artist, first 60, then title alone, first 25** | **4,087** | **0** |

Of the 46, 44 were found by the search but sat deeper than tenth, mostly title tracks:
a song that shares its name with its album comes back together with every other song
on that album. The other two have a title that punctuation breaks into very short
words. The six that the title search alone cannot rescue have titles of one short
word or a single character, which match half a library; they need the depth of the
first search instead.

Those 46 songs were then put through the real pipeline, each looked for by its own
title, artist, album and duration. With the old settings 2 were matched. With the new
ones all 46 are: 41 to the very entry, and 5 to another copy of the same recording
that the library also holds, such as the standard edition of an album where the song
looked for was on the deluxe one. The slowest of those searches took 1.2 seconds.

The same audit checked a real Daily Mix of 50 tracks, of which 34 had been reported as
not in the library. All 34 were confirmed: 33 are not in the library under any
spelling of title or artist, and one is there only as a different version (a solo
recording where the mix has the duet), which is rightly left out. So that mix came out
the same after the change, 16 matched and 34 left out, as it should.

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

`validate` needs no permission at all. `match`, `review`, `sync` and the `music-*`
commands need exactly one: **Automation**, to control Music.

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

**Accessibility permission is not needed** for anything above. Only the
[experimental catalog commands](#experimental-apple-music-catalog-support) use it. If
you granted it earlier and do not use those commands, you can switch it off again.

## Known limitations

- **Only songs already in your Music library are synced.** This is the design, not a
  gap: the rest of a Daily Mix is left out and listed.
- The Spotify export reads Spotify's web page, whose markup can change at any time.
  When it does, the export fails with a message and `extension/spotify_dom.js` needs
  updating. Its other limits are in the
  [extension README](extension/README.md#known-limits): local files and podcast
  episodes are left out, durations are whole seconds, and it has been used on one real
  Daily Mix so far.
- Each Daily Mix is exported by hand, one click per playlist; one command then syncs
  them all. Nothing runs on a schedule, and the browser does not start the sync.
- In a sync of several exports, a track you skip in review is not asked about again
  for the other mixes in that run, but it will be the next time.
- A song with a very common one-word title can still go unfound if it is neither
  among the first 60 results for title and artist nor among the first 25 for the title
  alone. On the library the depths were measured on, no song is in that position; a
  much larger library may need `search_limit` raised.
- A song is found by its title. If Spotify and Apple Music write the title itself
  differently (a translation, a different subtitle), neither search will reach it.
- A different version of a song (live, remix, acoustic, demo) is not accepted in place
  of the one in the Daily Mix. It shows up as "closest" in the left-out list.
- A song you skip in review is not remembered as skipped; you are asked again on the
  next run. `--no-review` avoids the questions.
- A dry run still remembers the matches it finds. It changes nothing in Music.
- The restore after a failed write is a best effort, not a transaction. It was tested
  with simulated failures, not by breaking the real Music app mid-write.
- Scoring was tuned on hand-written fixtures and spot-checked against a real library.
  Expect to adjust thresholds and penalties with wider use.
- Explicit and clean versions of a track are not told apart.
- A live recording is recognised from its title (`(Live)`, `- Live at …`), not from
  the album name alone.
- Part numbers in the title proper (`…, Pt. 1` vs `…, Pt. 2`) are only separated by
  duration. Inside brackets (`(Pt. 1)`) they are compared.
- Titles that are translated or transliterated differently on the two services will
  not match.
- For tracks without a Spotify ID, the cache key depends on the normalization rules.
  If those rules change, such a track is simply matched again.

## Experimental: Apple Music catalog support

**Not part of the supported workflow.** `sync` never does any of this. The code below
is kept, isolated, for possible future work on songs you do not have. It is reachable
only through commands whose names start with `experimental-`.

It differs from everything else in the tool in two ways:

- It operates the Music window through macOS Accessibility, so it needs that
  permission and takes over the screen while it runs.
- Two of its commands **add songs to your Music library**, and nothing in the tool can
  take a song out again.

```sh
python -m daily_mix_sync experimental-ui-inspect [--dump]
python -m daily_mix_sync experimental-catalog-search "Dreams" "Fleetwood Mac"
python -m daily_mix_sync experimental-catalog-add-test "Song" "Artist" [--yes]
python -m daily_mix_sync experimental-catalog-fill data/daily_mix_1.json [--dry-run] [--yes] [--no-review]
```

| Command | What it does | Changes Music? |
| --- | --- | --- |
| `experimental-ui-inspect` | Checks that the Music window has every part the automation relies on. `--dump` lists every element of the toolbar and main pane. Exit code 1 if something is missing. | Selects Search in the sidebar; nothing else |
| `experimental-catalog-search` | Searches the Apple Music catalog for one song and prints the results with their scores. | Leaves the search on screen; adds nothing |
| `experimental-catalog-add-test` | Does the same search, asks first (unless `--yes`), chooses Add to Library for the match, waits for it to show up, and prints its persistent ID. | **Adds one song to your library** |
| `experimental-catalog-fill` | For an export, finds the tracks your library lacks, asks first, and adds the catalog matches to your library. A later ordinary `sync` then picks them up like any other library song. `--dry-run` only lists what it would add. | **Adds songs to your library**; touches no playlist |

How it works, in short: select Search in the sidebar, switch the scope to Apple Music,
type the query, read the Songs results (title and artist only; the page shows no album
or duration), score them with the same matcher, open the chosen result's More menu and
choose Add to Library, then go back to AppleScript and wait for the song to appear in
the library before trusting it. Add to Library is the only menu item it ever chooses.

Before using any of it:

- **Accessibility permission** is required: System Settings → Privacy & Security →
  Accessibility, add the app you run the tool from. It is a broad permission: it lets
  that app operate any window on your Mac.
- **Do not use the Mac while it runs.** macOS only shows the Music window to a program
  while Music is in front, and the query is typed with real keystrokes. The tool
  checks that Music's search field has the keyboard before typing, and stops if not.
- **It assumes Music is in English** and reads only the first screen of song results.
- **It can add a different release** of the right recording, since no album is shown
  before adding, and a song it adds stays even if it then fails the full check.

Its tests: unit tests against a stand-in for the Music window (`tests/fake_ui.py`),
included in the default run, and six read-only live tests with their own switch,
`python -m pytest tests/integration --music-ui`.

**After a macOS or Music update** the window layout may change. Everything the code
knows about it is in `src/daily_mix_sync/music_ui.py`, and `ARCHITECTURE.md` records
the layout it was written against (Music 1.6.3, macOS 26.3). Run
`experimental-ui-inspect` to see which expected part is missing, `--dump` to see what
is there now, and adjust the matching handler at the top of that file. None of this
can affect `sync`, which does not use the window.

**What a later version would have to solve.** Two approaches were tried:

- *Add to the library first* (what the commands above do). It works and the song gets
  a durable identity, but it puts songs in your library, which is exactly what the
  supported workflow avoids.
- *Add straight to the playlist*, with Music's "Add songs to Library when adding to
  playlists" setting off. It works and keeps the library clean, but a track added this
  way has an identity only inside that playlist: its persistent ID changes every time
  it is added, the library cannot see it, and AppleScript cannot put it back once the
  playlist has been emptied. The cache and the empty-and-refill update would both need
  to change before this could be supported.

## Next steps

1. **Look at the near misses in review.** Real exports show songs that are plainly
   right yet land in review rather than being accepted: a song whose copy in the
   library sits on a compilation album (`Immigrant Song`, 89.8 where 90 is accepted),
   and an artist credited differently (Jimi Hendrix on Spotify, The Jimi Hendrix
   Experience in the library, 89.0). These are scoring questions, not search ones, and
   should be decided on real cases, not by moving a threshold.
2. **Run the whole routine for a week.** Export every Daily Mix, sync them all with one
   command, and note what gets in the way. That is the best guide to what, if
   anything, should be made easier next.
3. **A shorter path from browser to sync.** Today each mix is exported with a click and
   the files are moved into `data/` by hand. Whether that step is worth automating, and
   how, is better decided after some real use.
