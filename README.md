# Daily Mix Sync

A personal, local tool that recreates Spotify Daily Mix playlists in Apple Music.
It works from **track metadata only** (title, artist, album, duration). No audio is
downloaded, copied or transferred.

```
Spotify Daily Mix → track metadata → normalization → Apple Music search
                  → match scoring → Apple Music catalog ID → Apple Music playlist
```

## Status

Milestone 1 of 4 is done: the matching engine runs offline against a mock catalog.
**Nothing talks to Spotify or Apple Music yet.**

| Piece | State |
| --- | --- |
| Data models, normalization, candidate scoring | Working, tested |
| SQLite mapping cache | Working, tested |
| `match` command against a mock catalog | Working, tested |
| Real Apple Music catalog search | Not built (milestone 2) |
| Apple Music playlist creation | Not built (milestone 3) |
| Manual review command | Not built |
| Spotify browser extractor | Not built (milestone 4) |

## Setup

Requires Python 3.12 or newer. From the repository root:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Dependencies: [`rapidfuzz`](https://github.com/rapidfuzz/RapidFuzz) for fuzzy string
matching, and `pytest` for the tests. SQLite, JSON and logging come from the standard
library. An HTTP client will be added when the real Apple Music client is.

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
| `--mock-catalog FILE` | Search this JSON fixture instead of Apple Music. Required for now. |
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
need review) and `sync` (match, then create the Apple Music playlist).

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
| `database_path` | `data/mappings.sqlite3` | Mapping database for real runs (unused until milestone 2). |
| `search_limit` | 10 | Candidates requested per catalog search. |
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
| `apple_catalog_id` | The Apple Music catalog song ID |
| `score` | Match score when the mapping was stored |
| `method` | `auto` or `manual` |
| `matched_at` | When this pairing was first stored (UTC) |
| `source_title`, `source_artist`, `apple_title`, `apple_artist`, `apple_album` | Not used by the program; there so the table is readable |

A `manual` mapping is never overwritten by an `auto` one. To look at the cache:

```sh
sqlite3 -header -column data/mock_mappings.sqlite3 \
  "SELECT source_key, apple_catalog_id, score, method, apple_title FROM mappings"
```

Mock runs write to `data/mock_mappings.sqlite3`, never to the real cache, because mock
catalog IDs are made up and must not end up in a real playlist.

## Tests

```sh
python -m pytest
```

221 tests, all offline: normalization, scoring, the SQLite store, input validation,
config loading, the mock catalog, the pipeline end to end on the sample files, and the
CLI.

## How Spotify extraction will work

Spotify's Web API does not return the contents of Spotify-owned personalised playlists
such as Daily Mixes, so the track list has to come from the Spotify web player itself.

The plan (milestone 4) is a minimal Chrome Manifest V3 extension for `open.spotify.com`.
You open a Daily Mix, click the extension, and it reads the playlist title and the
track rows (title, artists, album, duration, track link) and saves them as a JSON file
in the format above. The track list is virtualised, so the script scrolls the list and
collects rows as they appear, keyed by track link, rather than assuming a fixed count.
Spotify-specific DOM selectors will be kept in one place.

## Apple Music requirements

Not needed for anything that works today. This is what milestones 2 and 3 will need,
checked against Apple's documentation on 2026-10-06.

**Accounts**

- An [Apple Developer Program](https://developer.apple.com/programs/) membership
  (paid), to create a MusicKit key.
- An Apple Music subscription on the Apple Account whose library gets the playlists.

**Developer token**, needed for every request, including catalog search
([docs](https://developer.apple.com/documentation/applemusicapi/generating-developer-tokens)):

- In the developer account, create a MusicKit private key (a `.p8` file, downloadable
  once) and note its 10-character Key ID and your 10-character Team ID.
- The token is a JWT signed with that key using ES256. Header: `alg: ES256`,
  `kid: <Key ID>`. Claims: `iss: <Team ID>`, `iat`, and `exp` at most 15777000 seconds
  (6 months) ahead.
- Sent as `Authorization: Bearer <developer token>`.

**Music User Token**, needed in addition for anything under `/v1/me`, which includes
creating playlists
([docs](https://developer.apple.com/documentation/applemusicapi/user-authentication-for-musickit)):

- Apple issues it through MusicKit on the web or MusicKit on Apple platforms after you
  sign in and approve access. It cannot be obtained from Python alone.
- The plan is one small local HTML page that loads MusicKit on the web, signs you in
  and shows the token, which you save to an ignored file. That is the only non-Python
  piece.
- Sent as `Music-User-Token: <token>`.

**Endpoints that will be used** (base `https://api.music.apple.com/v1`):

| Purpose | Request |
| --- | --- |
| Catalog search | `GET /catalog/{storefront}/search` |
| Create a playlist, optionally with tracks | `POST /me/library/playlists` |
| Append tracks to a playlist | `POST /me/library/playlists/{id}/tracks` |

Tracks are added as `{"id": "<catalog ID>", "type": "songs"}`. A 401 response means a
problem with the developer token; a 403 means a problem with the Music User Token.

**Where credentials will live:** environment variables and git-ignored files, never
source code. `.gitignore` already covers `config.json`, `.env*`, `secrets/`, `*.p8`,
`*.pem`, `*.key`, `*.token`, `tokens.json` and everything in `data/`.

## Known limitations

- Nothing is live yet; see Status.
- Scoring was tuned on hand-written fixtures. Expect to adjust thresholds and penalties
  once real catalog results are available.
- Apple's [playlist endpoints](https://developer.apple.com/documentation/applemusicapi/playlists-api)
  can create a library playlist and append tracks to it. None is documented for
  deleting a playlist or for removing, replacing or reordering its tracks. Each sync
  will therefore create a new playlist rather than update the previous one.
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

1. **Real catalog search** (milestone 2). Apple Music API client behind the same
   `search_songs()` method as the mock; developer-token generation from the `.p8` key;
   handling of 401/403, rate limiting and network errors; retune scoring on real
   results.
2. **Playlist creation** (milestone 3). Music User Token helper page, then create
   "Spotify Daily Mix N" and add the matched catalog IDs.
3. **Spotify extractor** (milestone 4). The Chrome extension described above.

Not tied to a milestone: the **manual review** command, a terminal prompt for tracks
that need review which saves the choice as a `manual` mapping. It needs nothing from
Apple, so it can be built at any point.
