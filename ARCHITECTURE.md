# Architecture

One Python package, one SQLite file, no services. Each stage is a module with plain
functions and dataclasses, so any stage can be run and tested on its own.

## Pipeline

```
playlist export (JSON)            importer.py     validate, drop duplicates
        │
        ▼
   SourceTrack ──► normalization  normalize.py    base title, flags, qualifiers, artists
        │
        ▼
   cache lookup                   database.py     source key → stored mapping?
        │ miss                         └── hit ──► done (CACHED)
        ▼
   Apple Music search             apple_music.py  search_songs(term, limit) → candidates
        │
        ▼
   candidate scoring              matcher.py      0–100 per candidate, best first
        │
        ├── score ≥ 90 ─► store mapping (auto)      MATCHED
        ├── score ≥ 75 ─► manual review (planned)   REVIEW
        └── otherwise ──► nothing stored            FAILED
        ▼
   playlist creation (planned)    apple_music.py
```

`sync.py` runs this loop for a playlist; `cli.py` parses arguments and prints the
report; `config.py` loads thresholds and weights; `models.py` holds the records.

Implemented today: everything except the real Apple Music client, the review prompt,
playlist creation and the Spotify extractor. `apple_music.py` contains only
`MockCatalog`, which searches a JSON fixture.

## Modules

| Module | Responsibility | Depends on |
| --- | --- | --- |
| `models.py` | `SourceTrack`, `AppleCandidate`, `Mapping` | nothing |
| `normalize.py` | Text cleanup, title/album/artist parsing, cache key | models |
| `matcher.py` | `MatchConfig`, scoring, accept / review / fail decision | normalize, rapidfuzz |
| `apple_music.py` | Catalog search (`MockCatalog` for now) | models, normalize |
| `database.py` | `MappingStore`: SQLite mapping cache | models, normalize |
| `importer.py` | Load and validate a playlist export | models, normalize |
| `config.py` | `Settings` from defaults plus optional JSON file | matcher |
| `sync.py` | The pipeline loop | all of the above |
| `cli.py` | Commands and output | all of the above |

The matcher never touches the network or the database: it takes a track and a list of
candidates and returns a decision. That is what makes it testable without Apple Music.

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
most 80, which lands in review. Equal scores keep the catalog's own search ranking.

All numbers are fields of `MatchConfig` and can be overridden in `config.json`.

## Mapping cache

- Key: `spotify:track:<id>` when the export has a Spotify ID, otherwise
  `meta:<artists>|<title>|<album>|<seconds>` built from normalized metadata.
- Any stored mapping is a cache hit and skips the search.
- Only automatic matches (and, later, manual confirmations) are stored. Tracks that
  need review or failed are evaluated again on the next run.
- A manual mapping is never replaced by an automatic one; the rule is enforced in the
  SQL upsert, not just by the caller.
- Each mapping is committed as soon as it is made, so an interrupted run keeps its
  progress.
- Mock runs use a separate database file so made-up catalog IDs cannot reach a real
  playlist.

## Deliberately absent

No server, no web framework, no ORM, no async, no plugin system. The one seam is
`search_songs(term, limit)`: the mock implements it now and the real Apple Music client
will implement it next.
