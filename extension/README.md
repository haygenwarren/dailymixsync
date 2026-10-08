# Daily Mix Sync: Spotify export

A small Chrome extension for personal use. You open a Daily Mix in the Spotify web
player, click the extension, and it saves the playlist's track list as a JSON file
that `daily_mix_sync` reads as it is.

It reads what the page shows: title, artists, album, duration and the link to each
track. It downloads no audio, talks to no Spotify API, and changes nothing in your
Spotify account.

## Install

There is no build step and nothing to download.

1. Open `chrome://extensions` in Chrome.
2. Switch on **Developer mode** (top right).
3. Click **Load unpacked** and choose this `extension/` folder.
4. Optional: click the puzzle-piece icon in the toolbar and pin **Daily Mix Sync**.

After changing any file here, click the reload arrow on the extension's card.

It should work in any Chromium browser that supports Manifest V3 (Brave, Edge, Arc).
Only Google Chrome has been tried.

## Use

1. Open <https://open.spotify.com> and go to a Daily Mix.
2. Click the extension's button, then **Export this playlist**.
3. Keep the popup open. The playlist scrolls by itself for a second or two, then
   goes back to where it was.
4. The popup says **Saved daily_mix_1.json**, with the number of tracks and the first
   and last song so you can compare them with the page. The file is in Chrome's
   download folder.

Then, from the repository root:

```sh
mv ~/Downloads/daily_mix_1.json data/
python -m daily_mix_sync validate data/daily_mix_1.json
python -m daily_mix_sync sync data/daily_mix_1.json --dry-run
```

`data/` is git-ignored, so your exports stay out of the repository.

It works on any playlist page, not only Daily Mixes, and it makes no assumption about
how many songs there are or how many Daily Mixes you have.

### What the popup can say

| It says | Meaning |
| --- | --- |
| This tab is not Spotify. | The current tab is not `open.spotify.com`. |
| This Spotify page is not a playlist. | You are on Spotify, but not on a playlist page. |
| Ready to export the playlist in this tab. | Click **Export this playlist**. |
| Reading tracks… 37 of 50 | It is scrolling and collecting. Closing the popup stops it and restores the scroll position. |
| Saved daily_mix_1.json | Done. Anything unusual is listed underneath; **Download again** saves the file a second time. |
| Export failed | It says what went wrong and, where it applies, which selector found nothing. **Copy debug info** puts the details on the clipboard. |

## The file

```json
{
  "playlist_name": "Daily Mix 1",
  "source_url": "https://open.spotify.com/playlist/…",
  "exported_at": "2026-10-07T12:00:00.000Z",
  "tracks": [
    {
      "title": "This Is What You Came For",
      "artist": "Calvin Harris, Rihanna",
      "album": "This Is What You Came For",
      "duration_ms": 222000,
      "spotify_track_id": "…",
      "spotify_url": "https://open.spotify.com/track/…"
    }
  ]
}
```

- **Text is passed on exactly as Spotify shows it.** Nothing is lower-cased, trimmed of
  "Remastered", or otherwise tidied; all of that is the Python application's job.
- Several artists are joined as `Artist One, Artist Two`, in Spotify's order.
- `duration_ms` is the duration shown, in whole milliseconds: `3:42` is `222000`,
  `1:03:15` is `3795000`. Spotify shows whole seconds, so that is the precision.
- `spotify_track_id` comes from the track link, and `spotify_url` is always the plain
  form `https://open.spotify.com/track/<id>` without tracking parameters.
- A value the page did not show is left out, never guessed. A track with no album or
  no duration is still exported; the popup tells you how many there were.
- `source_url` and `exported_at` are for your own reference. The Python importer
  ignores them.
- The file name comes from the playlist name: `Daily Mix 1` becomes
  `daily_mix_1.json`.

## How it reads the page

Spotify draws only the rows near the part of the list you can see, about 25 to 55 at
a time, and replaces them as you scroll. So the extension:

1. finds the track list and reads the playlist name from the page heading;
2. reads how many songs the list says it has;
3. reads every row currently in the page, storing each under **its position in the
   playlist**, which every row carries;
4. scrolls one step (a little less than the visible height, so consecutive views
   overlap), waits for Spotify to redraw, and reads again;
5. repeats until every position from 1 to the stated count has been read;
6. puts the scroll position back.

Storing rows by position is what makes this safe. A row read ten times is stored once.
A song that really is in the playlist twice is kept twice. And the export is known to
be complete, because a missing position is a gap that can be seen. If a row never
appears, the export **fails** and says which one; it does not save a shorter file.
The same goes for a playlist that changes while it is being read.

Rows that are not Spotify songs (podcast episodes, local files) are left out and
listed in the popup.

## Read-only

The extension must never change anything in your Spotify account, and it is built so
that it cannot do so by accident:

- The code that runs in the Spotify tab only reads the page. The single thing it
  changes is the scroll position of the playlist, which it restores.
- It never clicks, types, focuses or submits anything. So it cannot like or save a
  song, follow an artist, edit a playlist, play or pause, or touch the queue.
- It makes no network requests of any kind and reads no cookies or stored data.
- Nothing is sent anywhere. The result goes from the tab to the popup, and from the
  popup to a file on your disk.

`tests/extension/extension_files.test.js` enforces this by reading the source: a
click, a network call, a write to the page, or an assignment to anything other than
the scroll position makes the tests fail.

## Permissions

The manifest asks for two permissions and no access to any site.

| Permission | Why |
| --- | --- |
| `activeTab` | Lets the extension see the address of the current tab, and work in it, **only after you click its button** and only for that tab. Without a click it can see nothing. |
| `scripting` | Lets the popup put the three reading scripts into that tab when you click **Export**. |

Deliberately not requested:

- **No host permissions** (`https://open.spotify.com/*` or anything else). The
  extension has no standing access to Spotify; it does not run on Spotify pages until
  you click it. Chrome shows no permission warning when you load it.
- **No `downloads`.** The file is saved the way a web page saves one.
- **No `tabs`, `storage`, `cookies`, `webRequest`, background worker or content
  script that loads on its own.**

## When Spotify changes its page

Spotify's markup is not a public interface and will change. Everything the extension
knows about it is in one file, [`spotify_dom.js`](spotify_dom.js), with the observed
structure written out at the top.

| Name | Selector | Used for |
| --- | --- | --- |
| `tracklist` | `[data-testid="playlist-tracklist"]` | the track list |
| `playlistTitle` | `[data-testid="entityTitle"]` | the playlist name (fallback: the list's `aria-label`) |
| `row` | `[role="row"][aria-rowindex]` | one row; `aria-rowindex` gives its position |
| `headerCell` | `[role="columnheader"]` | telling the header row from song rows |
| `cell` | `[role="gridcell"]` | the cells of a row |
| `trackLink` | `a[href*="/track/"]` | title and track ID |
| `artistLink` | `a[href*="/artist/"]` | artists |
| `albumLink` | `a[href*="/album/"]` | album |
| `episodeLink` | `a[href*="/episode/"]` | recognising podcast episodes |

Also relied on: `aria-rowcount` on the track list (the number of rows including the
header), and an ancestor of the list with `overflow-y: scroll` (found by its computed
style, not by name). The duration is the text in a row that is written like a clock,
looked for from the last cell backwards. No generated class name is used anywhere.

### Debugging

- A failure names the selector that found nothing, for example
  *Looked for "artistLink" in spotify_dom.js: `a[href*="/artist/"]`*.
- **Copy debug info** (after a failure or a success) copies the page address, browser
  version, how many elements each selector matches now, every step with its timing,
  and the markup of one row with class names stripped. That row is usually all that is
  needed to correct a selector.
- Tick **Log each step to the page's console** before exporting to watch the same
  steps live in the Spotify tab's DevTools console, prefixed `[Daily Mix Sync]`.

## Tests

From the repository root:

```sh
npm install     # once; installs jsdom, used only by the tests
npm test
```

141 tests, no browser needed:

- `export_format.test.js`: duration parsing, track ID and canonical URL, file names,
  merging repeated rows, the JSON written.
- `spotify_dom.test.js`: reading rows from `fixtures/playlist_page.html`, a playlist
  page written the way Spotify writes it, with thirteen awkward rows (several
  artists, an artist with a comma in the name, an explicit badge, a song called
  "4:33", an hour-long track, non-Latin text, two songs with the same title, a
  podcast episode, a local file, a row with no album or duration).
- `scan.test.js`: the scroll loop against a simulated page that, like Spotify, holds
  only the rows near the viewport and redraws after a scroll. Lists from 1 to 300
  songs, slow redraws, rows that load late, starting part-way down, a wrong row
  count, a list that changes mid-read, cancelling.
- `extension_files.test.js`: the permissions and the read-only rules above.

The export of the fixture page has to equal `fixtures/expected_export.json` byte for
byte, and the Python suite (`tests/test_extension_export.py`) loads that same file
through the real importer. That is the contract between the two halves.

### Live check against Spotify

```sh
npm run test:live
```

This runs the real extension in a hidden copy of Chrome with a throwaway profile,
signed out, against four public playlists, clicks **Export** as you would, and
compares each downloaded file with what the page itself reports: the song count and
the track order from the page's own metadata. It also checks that the popup declines
on pages that are not playlists. It takes under a minute, needs Google Chrome and a
network connection, and never touches your own Chrome profile.

Run it when an export starts failing: it tells you whether Spotify's markup has moved.
Pass playlist addresses to check others, and `--keep DIR` to keep the exported files.

It cannot open a Daily Mix, which needs your account. For that, see the next section.

## Trying it on your own Daily Mixes

This part needs you, signed in. The first check has been done once, on one Daily Mix
of 50 tracks, on 2026-10-07; the file validated with all 50 usable. The rest have not
been reported.

1. Export a Daily Mix. Compare the popup's track count, first song and last song with
   the page.
2. Export a different Daily Mix and check that the file name and the playlist name
   follow it.
3. Scroll to the middle of a mix and export from there. The file should still start
   at the first song, and the page should end up where you left it.
4. Open a long playlist (a few hundred songs) and export it.
5. Run `python -m daily_mix_sync validate` on each file. Every track should be
   usable, with none skipped.

If any step fails, **Copy debug info** has what is needed to fix it.

## Known limits

- **Checked on public playlists, signed out, in Chrome 154, and on one real Daily Mix,
  signed in** (2026-10-07). Exporting a Daily Mix from mid-scroll, and a second mix,
  have not been reported.
- **Spotify can change its markup at any time.** The extension then fails with a
  message rather than exporting something wrong, and `spotify_dom.js` needs updating.
- **Local files and podcast episodes are left out.** They have no Spotify track
  link, and how Spotify draws a local file has not been seen.
- **Durations are whole seconds**, because that is what the page shows.
- **The popup has to stay open while it reads.** Clicking elsewhere closes it and
  stops the export; nothing is saved and the page is put back.
- If Chrome is set to ask where to save each file, the save dialog may close the
  popup. That setting has not been tried; if no file appears, export again.
- One playlist per click. There is no scheduled or background export, by design.
