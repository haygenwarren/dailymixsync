# Daily Mix Sync

A personal, Mac-only tool that recreates Spotify Daily Mix playlists in Apple Music.
It works from **track metadata only** (title, artist, album, duration). No audio is
downloaded, copied or transferred.

```
Spotify metadata → Python (normalize, match) → local Music library lookup
                 → AppleScript → optional Music UI automation → Apple Music playlist
```

Apple Music is reached by driving the **Music app on this Mac**, which is already
signed in to your subscription. There is no Apple Developer Program membership, no
MusicKit, no API key or token, and no hosted service.

## Status

| Piece | State |
| --- | --- |
| Data models, normalization, candidate scoring | Working, tested |
| SQLite mapping cache | Working, tested |
| `match` command against a mock catalog | Working, tested |
| Music app control through AppleScript: find library songs, create, fill, empty and delete managed playlists | Working, tested against the real app |
| `music-*` commands for trying the Music integration | Working |
| `match` against the Music library, and a `sync` command that writes a playlist | Not connected yet |
| Songs that are not in your library (Music UI automation) | Not built. Blocked until Accessibility permission is granted; only the permission check exists |
| Manual review command | Not built |
| Spotify browser extractor | Not built |

## Requirements

- **macOS** with the **Music** app. Developed and tested on macOS 26.3 with Music 1.6.3.
- **Python 3.12** or newer.
- Music **signed in** to an Apple Music subscription, with Sync Library on, so that
  catalog songs can live in your library and in playlists.
- **Automation permission** for the app you run the tool from; see
  [Permissions](#permissions).

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

## Try it

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

## Commands

Run everything from the repository root; relative paths such as `data/` are resolved
against the current directory.

```sh
python -m daily_mix_sync validate PLAYLIST.json
python -m daily_mix_sync match PLAYLIST.json --mock-catalog CATALOG.json [options]
```

**`validate`** parses a playlist export and lists its tracks with the cache key each
one will use. It reports duplicates and entries that are missing a title or artist.

**`match`** runs the matching pipeline and stores every automatic match.

| Option | Meaning |
| --- | --- |
| `--mock-catalog FILE` | Search this JSON fixture instead of Music. Required for now. |
| `--db FILE` | Mapping database. Default with a mock catalog: `data/mock_mappings.sqlite3`. |
| `--details` | Also list every matched track and the candidate chosen for it. |
| `--config FILE` | Settings file. Default: `./config.json` if present, else built-in defaults. |
| `-v` / `-vv` | Log each track's decision / also every candidate's score breakdown. |

Each track ends up in one of four states:

| State | Meaning |
| --- | --- |
| Cached | A stored mapping exists; no search is made. |
| New match | Best candidate scored at or above `auto_accept_threshold` (90); mapping stored. |
| Needs review | Best candidate scored between `review_threshold` (75) and 90; nothing stored. |
| Failed | No search results, or the best candidate scored below 75; nothing stored. |

Problems with the input, the config or the database are reported as a one-line
`error: ...` on stderr with exit code 1.

Planned commands, not built yet: `review` (pick a candidate by hand for tracks that
need review) and `sync` (match, then write the playlist in Music).

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
| `music-clear-test` | Empties `Spotify Daily Mix TEST`, or deletes it with `--delete`. | Only the TEST playlist |

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
| `database_path` | `data/mappings.sqlite3` | Mapping database for real runs (unused until `match` is connected to Music). |
| `search_limit` | 10 | Candidates requested per search. |
| `managed_playlist_prefix` | `Spotify Daily Mix` | The only playlists the tool may change; see [Playlist safety](#playlist-safety). |
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

A `manual` mapping is never overwritten by an `auto` one. To look at the cache:

```sh
sqlite3 -header -column data/mock_mappings.sqlite3 \
  "SELECT source_key, music_persistent_id, score, method, apple_title FROM mappings"
```

Mock runs write to `data/mock_mappings.sqlite3`, never to the real cache, because mock
track IDs are made up and must not end up in a real playlist. A database written
before the identifier column was renamed (it used to be `apple_catalog_id`) is
upgraded in place the first time it is opened.

## Tests

There are two kinds, kept apart.

**Unit tests** need nothing but Python and never touch Music:

```sh
python -m pytest
```

365 tests: normalization, scoring, the SQLite store, input validation, config, the
mock catalog, the pipeline end to end on the sample files, the CLI, and the Music
adapter. The adapter is tested against an in-memory stand-in for `osascript`
(`tests/fake_music.py`), which checks the arguments it sends, how it parses replies and
that it refuses to change unmanaged playlists.

**Live tests** drive the real Music app and are skipped unless you ask for them:

```sh
python -m pytest tests/integration --music-app
```

14 tests, about 20 seconds. They read your library and change exactly one playlist,
`Spotify Daily Mix TEST`: it is created if missing and, at the end, put back the way
it was found (or deleted if it did not exist). They also check that your library size
and every other playlist are the same afterwards. They need the Automation permission
below.

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

Everything Music-specific is in one module, `music_app.py`, which runs AppleScript
through `osascript`. Each command it uses was read from the scripting dictionary of
the installed app (`Music.app/Contents/Resources/com.apple.Music.sdef`) and then tried
against the real app. Nothing was taken from old iTunes examples.

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
| Delete a playlist and create it again | Works; the new one is a different playlist with a new persistent ID |
| Search the Apple Music catalog, or add a song that is not in the library | **Not possible through AppleScript.** The dictionary has no such command |

A song has the same persistent ID in the library and in every playlist it is in, which
is what makes that ID usable as the cached mapping.

**Updating a playlist** will therefore be done by emptying the managed playlist and
adding the new songs in order. That keeps the playlist's identity, so Music goes on
treating it as the same playlist rather than a new one. Delete-and-recreate also works
and remains the fallback.

Not exercised, because doing so would have meant disturbing your setup: starting
Music from cold (it was already running) and the path where Automation permission is
denied (it was already granted). The code for both exists and is unit tested.

### How library search behaves

Music's `search` command, as observed:

- Every word must match, as the beginning of a word, somewhere in the title, artist or
  album. Word order does not matter.
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

Until the Music integration has been validated end to end, the only playlist used is
`Spotify Daily Mix TEST`.

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

### Accessibility (needed for the next step, not for anything that works today)

Songs that are not in your library cannot be reached through AppleScript, so adding
them will mean operating the Music window itself: its search field, results and
menus. macOS requires a second permission for that:

1. Open **System Settings → Privacy & Security → Accessibility**.
2. Add the app you run the tool from, and switch it on.

`music-test` reports the current state on its last line:

```
Window control: not permitted. Only needed for songs that are not in your library:
                System Settings → Privacy & Security → Accessibility
```

Without it, macOS answers any attempt to look at the Music window with "osascript is
not allowed assistive access", which the tool reports as the instructions above. That
is the state this was developed in, so the layout of the Music window has not been
inspected yet and no UI automation has been written.

## Known limitations

- Only songs **already in your Music library** can be found and added. Songs that
  exist only in the Apple Music catalog are reported as not found.
- `match` and the planned `sync` are not connected to Music yet; today the Music
  integration is reachable through the `music-*` commands.
- UI automation, when it is built, will depend on the layout of the Music window and
  can break with a macOS update. It lives in its own module, `music_ui.py`, for that
  reason. It will also take over the Music window while it runs, and it needs the
  Accessibility permission, which is a broad one: it lets the app you grant it to
  operate any window on your Mac.
- Scoring was tuned on hand-written fixtures and spot-checked against a real library.
  Expect to adjust thresholds and penalties with wider use.
- Music returns playlist tracks in the playlist's current sort order. A playlist left
  in its default order reads back in the order songs were added; one you have re-sorted
  by a column in Music will not.
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

1. **Catalog songs through the Music window.** Needs Accessibility permission first.
   Then: inspect the accessibility hierarchy of the installed Music app and prove, for
   one song that is not in the library, that it can be found and added to
   `Spotify Daily Mix TEST`. This goes in `music_ui.py`.
2. **Connect Music to the pipeline.** Let `match` search the Music library, check that
   cached tracks still exist, and add a `sync` command that empties and refills the
   managed playlist in source order.
3. **Spotify extractor.** The Chrome extension described above.

Not tied to a step: the **manual review** command, a terminal prompt for tracks that
need review which saves the choice as a `manual` mapping.
