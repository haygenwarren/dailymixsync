// A stand-in for a Spotify playlist page, for tests.
//
// The markup follows what open.spotify.com served on 2026-10-07 (see the notes at the
// top of extension/spotify_dom.js), down to the wrappers, badges and buttons that the
// extension has to ignore. Class names are made up, as Spotify's are generated.
//
// FakePlaylistPage adds the behaviour that makes the real page hard to read: only the
// rows near the viewport exist, they are redrawn a moment after a scroll, and rows
// that have not been loaded yet are not drawn at all.
"use strict";

const { JSDOM } = require("jsdom");

const PLAYLIST_URL = "https://open.spotify.com/playlist/FIXTUREPLAYLIST0000001";

// No scan should need anywhere near this much simulated time. One that does is not
// going to end, and is stopped so that the test fails instead of hanging.
const CLOCK_LIMIT_MS = 10 * 60 * 1000;

// A scan looks at the clock a handful of times between pauses. One that looks this
// often without pausing is spinning, which in a browser would freeze the tab.
const SPIN_LIMIT = 100000;

// Count one look at a simulated clock; `counter.reads` is set back to 0 by a pause.
function countRead(counter) {
  counter.reads += 1;
  if (counter.reads > SPIN_LIMIT) {
    throw new Error("the scan read the clock 100,000 times without pausing");
  }
}

// Move a simulated clock on. No real time is spent, but Node's event loop gets a turn.
async function advance(clock, ms) {
  await new Promise((resolve) => setImmediate(resolve));
  if (clock + ms > CLOCK_LIMIT_MS) {
    throw new Error("the scan ran for ten simulated minutes without ending");
  }
  return clock + ms;
}

function escapeHtml(value) {
  return String(value).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function fakeId(prefix, text) {
  let hash = 0;
  for (const char of text) hash = (hash * 31 + char.codePointAt(0)) >>> 0;
  return `${prefix}${hash.toString(36).toUpperCase()}`.padEnd(22, "0").slice(0, 22);
}

// A numbered list of plain songs, for tests that only care about how many there are.
function makeTracks(count, prefix = "Song") {
  return Array.from({ length: count }, (_, i) => ({
    id: `TRACK${String(i + 1).padStart(17, "0")}`,
    title: `${prefix} ${i + 1}`,
    artists: [`Artist ${(i % 7) + 1}`],
    album: `Album ${(i % 5) + 1}`,
    duration: `${3 + (i % 3)}:${String((i * 7) % 60).padStart(2, "0")}`,
  }));
}

// One row of the track list. `position` counts songs from 1; the row's aria-rowindex
// is one higher because the header row is row 1.
//
// track: { id, title, artists, album, duration, explicit, href, kind }
//   kind "episode": a podcast episode; kind "local": a local file, with no links.
function rowHtml(track, position, options = {}) {
  const kind = track.kind || "track";
  // options.rowIndex: false leaves the attribute out; a string is written as it is.
  const rowIndex =
    options.rowIndex === false
      ? ""
      : ` aria-rowindex="${typeof options.rowIndex === "string" ? options.rowIndex : position + 1}"`;
  const artists = track.artists || [];
  const by = artists.join(", ");

  let titleHtml;
  let artistHtml;
  let albumHtml;
  if (kind === "local") {
    titleHtml = `<div class="e-text e-body-medium one-line" data-encore-id="text" dir="auto">${escapeHtml(track.title)}</div>`;
    artistHtml = `<span class="one-line" dir="auto">${escapeHtml(by)}</span>`;
    albumHtml = track.album ? `<span class="one-line" dir="auto">${escapeHtml(track.album)}</span>` : "";
  } else {
    const section = kind === "episode" ? "episode" : "track";
    const href = track.href || `/${section}/${track.id}`;
    const ownerSection = kind === "episode" ? "show" : "artist";
    titleHtml =
      `<a draggable="false" class="kCUv" data-testid="internal-track-link" href="${escapeHtml(href)}" tabindex="-1">` +
      `<div class="e-text e-body-medium kCUv one-line" data-encore-id="text" dir="auto">${options.padTitle ? "\n          " : ""}${escapeHtml(track.title)}${options.padTitle ? "\n        " : ""}</div></a>`;
    const linkFor = options.artistHref || ((name) => `/${ownerSection}/${fakeId("A", name)}`);
    artistHtml = artists
      .map((name) => `<a draggable="true" dir="auto" href="${linkFor(name)}" tabindex="-1">${escapeHtml(name)}</a>`)
      .join(", ");
    albumHtml = track.album
      ? `<a draggable="true" class="one-line" dir="auto" href="/album/${fakeId("B", track.album)}" tabindex="-1">${escapeHtml(track.album)}</a>`
      : "";
  }
  const badge = track.explicit
    ? `<span role="img" aria-label="Explicit" class="e-tag" data-encore-id="tagIcon" title="Explicit">E</span>`
    : "";
  const duration = track.duration
    ? `<div class="e-text e-body-small subdued" data-encore-id="text">${escapeHtml(track.duration)}</div>`
    : "";
  const label = escapeHtml(`${track.title} by ${by}`);

  return (
    `<div role="row"${rowIndex} aria-selected="false">` +
    `<div data-testid="tracklist-row" class="AWIr hcaz" draggable="true" role="presentation">` +
    `<div class="scCF" role="gridcell" aria-colindex="1"><div class="nNpU">` +
    `<span class="e-text e-body-medium tZHp" data-encore-id="text">${position}</span>` +
    `<button class="uguM" aria-label="Play ${label}" tabindex="-1"><svg data-encore-id="icon" role="img" aria-hidden="true" viewBox="0 0 24 24"></svg></button>` +
    `</div></div>` +
    `<div class="fwyE" role="gridcell" aria-colindex="2">` +
    `<img aria-hidden="false" draggable="false" loading="eager" alt="" width="40" height="40">` +
    `<div class="sdNB">${titleHtml}` +
    `<span class="e-text e-body-medium subdued rJU8" data-encore-id="text">${badge}</span>` +
    `<span class="e-text e-body-small subdued h0Bl one-line" data-encore-id="text"><div class="e-text e-body-small ravq" data-encore-id="text">${artistHtml}</div></span>` +
    `</div></div>` +
    `<div class="Xn3S" role="gridcell" aria-colindex="3"><span class="e-text e-body-small" data-encore-id="text">${albumHtml}</span></div>` +
    `<div class="Xn3S" role="gridcell" aria-colindex="4"><span class="e-text e-body-small subdued one-line" data-encore-id="text">2 weeks ago</span></div>` +
    `<div class="PGUs" role="gridcell" aria-colindex="5">` +
    `<button aria-checked="false" class="e-button" aria-label="Add to Liked Songs" data-encore-id="buttonTertiary" tabindex="-1"><span aria-hidden="true"><svg data-encore-id="icon" role="img" aria-hidden="true" viewBox="0 0 16 16"></svg></span></button>` +
    duration +
    `<button aria-haspopup="menu" class="e-button" data-testid="more-button" aria-label="More options for ${label}" data-encore-id="buttonTertiary" tabindex="-1"><span aria-hidden="true"><svg data-encore-id="icon" role="img" aria-hidden="true" viewBox="0 0 16 16"></svg></span></button>` +
    `</div>` +
    `</div></div>`
  );
}

function headerRowHtml() {
  const column = (index, label) =>
    `<div class="Xn3S" role="columnheader" aria-colindex="${index}" aria-sort="none" tabindex="-1">` +
    `<div data-testid="column-header-context-menu"><div class="AYoG"><span class="e-text e-body-small one-line" data-encore-id="text">${label}</span></div></div>` +
    `<div role="separator" aria-orientation="horizontal" aria-label="Resize columns" tabindex="0" draggable="false" data-skip-in-keyboard-nav="true"></div></div>`;
  return (
    `<div class="WPrl hcaz" role="row" aria-rowindex="1">` +
    `<div class="scCF" role="columnheader" aria-colindex="1" aria-sort="none" tabindex="-1"><div data-testid="column-header-context-menu">#</div></div>` +
    column(2, "Title") +
    column(3, "Album") +
    column(4, "Date added") +
    `<div class="PGUs" role="columnheader" aria-colindex="5" aria-sort="none" tabindex="-1"><div data-testid="column-header-context-menu"><div aria-label="Duration" class="AYoG"><svg data-encore-id="icon" role="img" aria-hidden="true" viewBox="0 0 16 16"></svg></div></div></div>` +
    `</div>`
  );
}

const PLACEHOLDER = `<div class="hcaz" data-testid="tracklist-row-placeholder"><div class="Xid3"></div><div class="Xid3"></div><div class="Xid3" data-hidden="true"></div></div>`;

// The whole page. `rowsHtml` is what the track list holds to begin with.
//
// options: title (false leaves out the heading), gridLabel (false leaves out the
// aria-label), rowCount (false leaves out aria-rowcount; a number overrides it),
// header (false leaves out the header row), tracklist (false leaves out the list).
function pageHtml(name, songCount, rowsHtml, options = {}) {
  const safeName = escapeHtml(name);
  const header = options.header === false ? "" : headerRowHtml();
  const headerRows = options.header === false ? 0 : 1;
  const rowCount = options.rowCount === undefined ? songCount + headerRows : options.rowCount;
  const rowCountAttribute = rowCount === false ? "" : ` aria-rowcount="${rowCount}"`;
  const labelAttribute = options.gridLabel === false ? "" : ` aria-label="${safeName}"`;
  const title =
    options.title === false
      ? ""
      : `<span dir="auto" class="jucW" draggable="true" data-testid="entityTitle"><h1 class="e-text e-headline-large" data-encore-id="text" dir="auto" style="font-size: 6rem;">${safeName}</h1></span>`;
  const tracklist =
    options.tracklist === false
      ? ""
      : `<div role="grid"${rowCountAttribute} aria-colcount="5"${labelAttribute} class="F2EQ SDyH" tabindex="0" data-testid="playlist-tracklist" style="--row-height: 56px;">` +
        `<div class="N87s" data-scroll-size-invariant-class="" role="presentation" style="top: 64px;"><div role="presentation">${header}</div>` +
        `<button data-skip-in-keyboard-nav="true" class="e-button" aria-label="Change visible columns" data-encore-id="buttonTertiary"><span aria-hidden="true"><svg data-encore-id="icon" role="img" aria-hidden="true" viewBox="0 0 16 16"></svg></span></button></div>` +
        `<div class="RqGU" role="presentation" data-scroll-size-contained="" style="height: ${songCount * 56}px;">` +
        `<div data-testid="top-sentinel" class="XNT0" role="presentation">${PLACEHOLDER}<div role="presentation"></div></div>` +
        `<div role="presentation" style="transform: translateY(0px);">${rowsHtml}</div>` +
        `<div data-testid="bottom-sentinel" class="XNT0" role="presentation"><div role="presentation"></div>${PLACEHOLDER}</div>` +
        `</div></div>`;

  // Around the track list: a library sidebar with its own grid and heading, and a
  // player bar that links to a track. None of it belongs in an export.
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>${safeName} | Spotify Playlist</title>
</head>
<body>
<div id="main" class="Root">
<nav aria-label="Main" class="Xz0G">
<h1 class="e-text e-body-medium-bold" data-encore-id="text">Your Library</h1>
<div role="grid" aria-rowcount="2" aria-colcount="1" aria-label="Your Library" tabindex="0">
<div role="row" aria-rowindex="1"><div role="gridcell" aria-colindex="1"><a href="/playlist/OTHERPLAYLIST000000001">Another playlist</a></div></div>
<div role="row" aria-rowindex="2"><div role="gridcell" aria-colindex="1"><a href="/album/SIDEBARALBUM0000000001">A saved album</a></div></div>
</div>
</nav>
<div class="main-view-container">
<div class="" data-overlayscrollbars-viewport="scrollbarHidden overflowXHidden overflowYScroll" tabindex="-1" style="overflow-y: scroll;">
<div class="main-view-container__scroll-node-child">
<main class="Thei" tabindex="-1" aria-label="${safeName} | Spotify Playlist">
<section role="presentation" data-testid="playlist-page" data-test-uri="spotify:playlist:FIXTUREPLAYLIST0000001" class="gD0W">
<div class="yLg7" data-testid="entity-header">
<span class="e-text e-body-small" data-encore-id="text">Public Playlist</span>
${title}
<span class="e-text e-body-small subdued" data-encore-id="text">${songCount} songs, about 3 hr</span>
</div>
<div data-testid="action-bar-row"><button data-testid="play-button" aria-label="Play" data-encore-id="buttonPrimary"><span aria-hidden="true"><svg data-encore-id="icon" role="img" aria-hidden="true" viewBox="0 0 16 16"></svg></span></button></div>
<div class="contentSpacing">
${tracklist}
</div>
</section>
</main>
</div>
</div>
</div>
<footer data-testid="now-playing-bar">
<a data-testid="context-item-link" href="/track/NOWPLAYINGTRACK0000001">A song in the player bar</a>
<a href="/artist/NOWPLAYINGARTIST000001">Its artist</a>
<div data-testid="playback-position">1:07</div><div data-testid="playback-duration">3:30</div>
</footer>
</div>
</body>
</html>
`;
}

// A whole page with every row present, as jsdom parses it.
function staticPage(name, tracks, options = {}) {
  const rows = tracks.map((track, i) => rowHtml(track, i + 1, options)).join("\n");
  const dom = new JSDOM(pageHtml(name, tracks.length, rows, options), { url: options.url || PLAYLIST_URL });
  return dom.window.document;
}

// What scanPlaylist needs to read a page that never changes, on a clock of its own.
function staticEnv(document, extra = {}) {
  let clock = 0;
  const counter = { reads: 0 };
  return {
    document,
    sleep: async (ms) => {
      counter.reads = 0;
      clock = await advance(clock, ms);
    },
    now: () => {
      countRead(counter);
      return clock;
    },
    timestamp: () => "2026-10-07T12:00:00.000Z",
    ...extra,
  };
}

class FakePlaylistPage {
  // tracks: the playlist, in order.
  // options:
  //   name, url, startTop     the playlist name, the page address, where it is scrolled to
  //   viewport, rowHeight     heights in pixels
  //   overscan                rows drawn above and below the visible ones
  //   redrawMs                how long after a scroll the rows are redrawn
  //   chunk, loadMs           rows load `chunk` at a time, each chunk taking loadMs
  //                           once it is first needed; the first chunk is there at once
  //   neverLoadFrom           rows from this position on never load
  //   page                    passed to pageHtml (see there)
  //   row                     passed to rowHtml for every row
  constructor(tracks, options = {}) {
    this.tracks = tracks;
    this.name = options.name === undefined ? "Daily Mix 1" : options.name;
    this.viewport = options.viewport || 667;
    this.rowHeight = options.rowHeight || 56;
    this.overscan = options.overscan === undefined ? 12 : options.overscan;
    this.redrawMs = options.redrawMs === undefined ? 100 : options.redrawMs;
    this.chunk = options.chunk || 100;
    this.loadMs = options.loadMs || 0;
    this.neverLoadFrom = options.neverLoadFrom || Infinity;
    this.rowOptions = options.row || {};
    this.listTop = 478; // where the first row sits below the page header
    this.footer = 320;

    this.clock = 0;
    this.reads = 0; // looks at the clock since the last pause
    this.redrawAt = null;
    this.loaded = new Set([0]);
    this.loading = new Map(); // chunk -> time it arrives
    this.timers = []; // { at, run }
    this.scrollWrites = [];
    this.draws = 0;

    const pageOptions = options.page || {};
    this.dom = new JSDOM(pageHtml(this.name, tracks.length, "", pageOptions), { url: options.url || PLAYLIST_URL });
    this.document = this.dom.window.document;
    this.scroller = this.document.querySelector("[data-overlayscrollbars-viewport]");
    this.top = 0;
    const page = this;
    Object.defineProperty(this.scroller, "scrollTop", {
      configurable: true,
      get() {
        return page.top;
      },
      set(value) {
        page.scrollWrites.push(value);
        const top = Math.max(0, Math.min(Math.round(Number(value) || 0), page.maxTop()));
        if (top !== page.top) {
          page.top = top;
          page.redrawAt = page.clock + page.redrawMs;
        }
      },
    });
    Object.defineProperty(this.scroller, "clientHeight", { configurable: true, get: () => page.viewport });
    Object.defineProperty(this.scroller, "scrollHeight", { configurable: true, get: () => page.height() });

    this.top = Math.max(0, Math.min(options.startTop || 0, this.maxTop()));
    this.draw();
    this.scrollWrites = [];
  }

  height() {
    return this.listTop + this.tracks.length * this.rowHeight + this.footer;
  }

  maxTop() {
    return Math.max(0, this.height() - this.viewport);
  }

  rowsContainer() {
    const sentinel = this.document.querySelector('[data-testid="top-sentinel"]');
    return sentinel ? sentinel.nextElementSibling : null;
  }

  // The positions (from 1) the list would draw at the current scroll position.
  window() {
    const count = this.tracks.length;
    const firstVisible = Math.floor((this.top - this.listTop) / this.rowHeight) + 1;
    const lastVisible = Math.floor((this.top + this.viewport - this.listTop) / this.rowHeight) + 1;
    const first = Math.max(1, firstVisible - this.overscan);
    const last = Math.min(count, lastVisible + this.overscan);
    return first <= last ? [first, last] : null;
  }

  isLoaded(position) {
    if (position >= this.neverLoadFrom) return false;
    if (!this.loadMs) return true;
    const chunk = Math.floor((position - 1) / this.chunk);
    if (this.loaded.has(chunk)) return true;
    if (!this.loading.has(chunk)) this.loading.set(chunk, this.clock + this.loadMs);
    return false;
  }

  draw() {
    const container = this.rowsContainer();
    if (!container) return;
    this.draws += 1;
    const range = this.window();
    const html = [];
    let firstDrawn = null;
    if (range) {
      for (let position = range[0]; position <= range[1]; position += 1) {
        if (!this.isLoaded(position)) continue;
        if (firstDrawn === null) firstDrawn = position;
        html.push(rowHtml(this.tracks[position - 1], position, this.rowOptions));
      }
    }
    container.setAttribute("style", `transform: translateY(${((firstDrawn || 1) - 1) * this.rowHeight}px);`);
    container.innerHTML = html.join("");
  }

  // Run `run` once the clock reaches `at`.
  at(at, run) {
    this.timers.push({ at, run });
  }

  // Let `ms` pass: anything due by then happens, in order.
  async sleep(ms) {
    this.reads = 0;
    this.clock = await advance(this.clock, ms);
    let redraw = false;
    if (this.redrawAt !== null && this.clock >= this.redrawAt) {
      this.redrawAt = null;
      redraw = true;
    }
    for (const [chunk, arrives] of [...this.loading]) {
      if (this.clock >= arrives) {
        this.loading.delete(chunk);
        this.loaded.add(chunk);
        redraw = true;
      }
    }
    const due = this.timers.filter((timer) => this.clock >= timer.at);
    this.timers = this.timers.filter((timer) => this.clock < timer.at);
    for (const timer of due) {
      if (timer.run(this) !== false) redraw = true;
    }
    if (redraw) this.draw();
  }

  // What scanPlaylist needs, on this page's clock.
  env(extra = {}) {
    return {
      document: this.document,
      sleep: (ms) => this.sleep(ms),
      now: () => {
        countRead(this);
        return this.clock;
      },
      timestamp: () => "2026-10-07T12:00:00.000Z",
      ...extra,
    };
  }
}

module.exports = { PLAYLIST_URL, FakePlaylistPage, makeTracks, rowHtml, pageHtml, staticPage, staticEnv, fakeId };
