"use strict";

// The scroll-and-collect loop, against a simulated Spotify page that draws only the
// rows near the viewport (helpers/spotify_page.js). No browser, no real waiting: the
// page has its own clock, which moves when the scan sleeps.

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

require("../../extension/export_format.js");
const dom = require("../../extension/spotify_dom.js");
const { scanPlaylist, describeError, ScanError, TIMING } = require("../../extension/content.js");
const { PLAYLIST_URL, FakePlaylistPage, makeTracks, staticPage, staticEnv } = require("./helpers/spotify_page.js");

const FIXTURES = path.join(__dirname, "fixtures");

async function scan(page, extra) {
  const result = await scanPlaylist(page.env(extra));
  return { result, data: JSON.parse(result.json) };
}

function titles(data) {
  return data.tracks.map((track) => track.title);
}

async function failure(page, extra) {
  try {
    await scanPlaylist(page.env(extra));
  } catch (error) {
    return error;
  }
  throw new Error("the scan was expected to fail");
}

test("the simulated page really does hold only some of the rows", () => {
  const page = new FakePlaylistPage(makeTracks(120));
  const drawn = dom.renderedPositions(dom.findTracklist(page.document), 1);
  assert.ok(drawn.length < 40, `${drawn.length} rows drawn`);
  assert.equal(drawn[0], 1);
  page.scroller.scrollTop = 3000;
  assert.equal(dom.renderedPositions(dom.findTracklist(page.document), 1)[0], 1, "not redrawn until a moment later");
});

test("a playlist that fits on screen is exported without scrolling", async () => {
  const page = new FakePlaylistPage(makeTracks(8));
  const { result, data } = await scan(page);
  assert.equal(result.trackCount, 8);
  assert.deepEqual(titles(data), makeTracks(8).map((track) => track.title));
  assert.deepEqual(page.scrollWrites, [0], "only put back where it was");
});

test("every track of a fifty-song playlist is collected, in order", async () => {
  const tracks = makeTracks(50);
  const page = new FakePlaylistPage(tracks);
  const { result, data } = await scan(page);
  assert.equal(result.trackCount, 50);
  assert.deepEqual(titles(data), tracks.map((track) => track.title));
  assert.deepEqual(data.tracks.map((track) => track.spotify_track_id), tracks.map((track) => track.id));
  assert.ok(page.scrollWrites.length > 2, "it had to scroll");
});

test("the length is whatever the playlist has, not fifty", async () => {
  for (const count of [1, 13, 49, 51, 87, 237]) {
    const tracks = makeTracks(count);
    const { result, data } = await scan(new FakePlaylistPage(tracks));
    assert.equal(result.trackCount, count);
    assert.deepEqual(titles(data), tracks.map((track) => track.title), `${count} tracks`);
  }
});

test("rows read many times while scrolling are exported once each", async () => {
  const tracks = makeTracks(150);
  const { data } = await scan(new FakePlaylistPage(tracks, { overscan: 20 }));
  const ids = data.tracks.map((track) => track.spotify_track_id);
  assert.equal(ids.length, 150);
  assert.equal(new Set(ids).size, 150);
});

test("a song that is in the playlist twice is exported twice", async () => {
  const tracks = makeTracks(60);
  tracks[44] = { ...tracks[2] };
  const { data } = await scan(new FakePlaylistPage(tracks));
  assert.equal(data.tracks.length, 60);
  assert.deepEqual(data.tracks[44], data.tracks[2]);
});

test("the scroll position is put back where it was", async () => {
  for (const startTop of [0, 700, 2345]) {
    const page = new FakePlaylistPage(makeTracks(120), { startTop });
    await scan(page);
    assert.equal(page.scroller.scrollTop, startTop);
    assert.equal(page.scrollWrites.at(-1), startTop);
  }
});

test("a playlist that was scrolled far down is still read from the top", async () => {
  const tracks = makeTracks(200);
  const page = new FakePlaylistPage(tracks, { startTop: 9000 });
  assert.ok(dom.renderedPositions(dom.findTracklist(page.document), 1)[0] > 100);
  const { data } = await scan(page);
  assert.deepEqual(titles(data), tracks.map((track) => track.title));
  assert.equal(page.scroller.scrollTop, 9000);
});

test("a playlist scrolled past its last row is still read", async () => {
  const tracks = makeTracks(30);
  const page = new FakePlaylistPage(tracks, { overscan: 0, viewport: 200, startTop: 99999 });
  assert.deepEqual(dom.renderedPositions(dom.findTracklist(page.document), 1), [], "no rows drawn to begin with");
  const { data } = await scan(page);
  assert.equal(data.tracks.length, 30);
});

test("a page that redraws slowly is waited for", async () => {
  const tracks = makeTracks(90);
  const { data } = await scan(new FakePlaylistPage(tracks, { redrawMs: 450 }));
  assert.deepEqual(titles(data), tracks.map((track) => track.title));
});

test("a page slower than one wait still comes out complete", async () => {
  for (const redrawMs of [TIMING.settleMs + 500, TIMING.settleMs * 3]) {
    const tracks = makeTracks(90);
    const { data } = await scan(new FakePlaylistPage(tracks, { redrawMs }));
    assert.deepEqual(titles(data), tracks.map((track) => track.title), `redraw after ${redrawMs} ms`);
  }
});

test("a page too slow to read fails instead of exporting short", async () => {
  const page = new FakePlaylistPage(makeTracks(90), { redrawMs: TIMING.stallMs * 5, startTop: 120 });
  const error = await failure(page);
  assert.equal(error.step, "stalled");
  assert.match(error.message, /never appeared/);
  assert.equal(page.scroller.scrollTop, 120);
});

test("rows that load late are waited for", async () => {
  const tracks = makeTracks(260);
  const page = new FakePlaylistPage(tracks, { chunk: 50, loadMs: 900 });
  const { data } = await scan(page);
  assert.deepEqual(titles(data), tracks.map((track) => track.title));
});

test("rows that take seconds to load are waited for", async () => {
  const tracks = makeTracks(120);
  const { data } = await scan(new FakePlaylistPage(tracks, { chunk: 40, loadMs: 4000 }));
  assert.equal(data.tracks.length, 120);
});

test("small windows and little overscan still give every row", async () => {
  const tracks = makeTracks(140);
  const { data } = await scan(new FakePlaylistPage(tracks, { viewport: 260, overscan: 1 }));
  assert.deepEqual(titles(data), tracks.map((track) => track.title));
});

test("a very tall window still gives every row", async () => {
  const tracks = makeTracks(300);
  const { data } = await scan(new FakePlaylistPage(tracks, { viewport: 2400, overscan: 3 }));
  assert.deepEqual(titles(data), tracks.map((track) => track.title));
});

test("progress is reported as rows are read, ending at the full count", async () => {
  const seen = [];
  await scan(new FakePlaylistPage(makeTracks(130)), { onProgress: (state) => seen.push(state) });
  assert.ok(seen.length > 3);
  assert.ok(seen.every((state) => state.expected === 130));
  assert.deepEqual(seen.map((state) => state.collected), [...seen.map((state) => state.collected)].sort((a, b) => a - b));
  assert.equal(seen.at(-1).collected, 130);
});

test("the result carries what the popup shows", async () => {
  const { result } = await scan(new FakePlaylistPage(makeTracks(70), { name: "Daily Mix 3" }));
  assert.equal(result.playlistName, "Daily Mix 3");
  assert.equal(result.filename, "daily_mix_3.json");
  assert.equal(result.trackCount, 70);
  assert.equal(result.rowCount, 70);
  assert.equal(result.first, "Song 1 — Artist 1");
  assert.equal(result.last, "Song 70 — Artist 7");
  assert.deepEqual(result.warnings, []);
});

test("the export says which playlist and when, without the address's parameters", async () => {
  const page = new FakePlaylistPage(makeTracks(5), { url: `${PLAYLIST_URL}?si=abc123#top` });
  const { data } = await scan(page);
  assert.deepEqual(Object.keys(data), ["playlist_name", "source_url", "exported_at", "tracks"]);
  assert.equal(data.playlist_name, "Daily Mix 1");
  assert.equal(data.source_url, PLAYLIST_URL);
  assert.equal(data.exported_at, "2026-10-07T12:00:00.000Z");
});

test("the fixture page exports exactly the expected file", async () => {
  const html = fs.readFileSync(path.join(FIXTURES, "playlist_page.html"), "utf8");
  const doc = new JSDOM(html, { url: PLAYLIST_URL }).window.document;
  const result = await scanPlaylist(staticEnv(doc));
  const expected = fs.readFileSync(path.join(FIXTURES, "expected_export.json"), "utf8");
  assert.equal(result.json, expected);
  assert.equal(result.trackCount, 11);
  assert.equal(result.rowCount, 13);
});

test("rows that are not songs are left out and said so", async () => {
  const tracks = makeTracks(40);
  tracks[4] = { kind: "episode", id: "EPISODE0000000000000001", title: "A Podcast Episode", artists: ["A Show"] };
  tracks[30] = { kind: "local", title: "Garage Demo", artists: ["My Old Band"] };
  const { result, data } = await scan(new FakePlaylistPage(tracks));
  assert.equal(result.trackCount, 38);
  assert.equal(result.rowCount, 40);
  assert.equal(data.tracks.length, 38);
  assert.ok(!titles(data).includes("A Podcast Episode") && !titles(data).includes("Garage Demo"));
  assert.equal(result.warnings.length, 1);
  assert.match(result.warnings[0], /^2 of 40 rows left out: row 5 \(a podcast episode, not a song\); row 31 \(no link to a Spotify track/);
  assert.equal(data.tracks[4].title, "Song 6", "the rows after a left-out one keep their order");
});

test("a long list of left-out rows is summarised", async () => {
  const tracks = makeTracks(20).map((track, i) => (i < 8 ? { kind: "local", title: `Local ${i}`, artists: ["Me"] } : track));
  const { result } = await scan(new FakePlaylistPage(tracks));
  assert.match(result.warnings[0], /^8 of 20 rows left out: row 1 .*; row 5 [^;]*; and 3 more\.$/);
});

test("missing albums, durations and IDs are warned about, not invented", async () => {
  const tracks = makeTracks(12).map((track) => ({ ...track, album: "", duration: "" }));
  tracks[3].href = "/track/";
  const { result, data } = await scan(new FakePlaylistPage(tracks));
  assert.deepEqual(result.warnings, [
    "No album name found for any track.",
    "No duration found for any track.",
    "1 of 12 tracks have no Spotify track ID.",
  ]);
  assert.deepEqual(Object.keys(data.tracks[0]), ["title", "artist", "spotify_track_id", "spotify_url"]);
  assert.deepEqual(Object.keys(data.tracks[3]), ["title", "artist"]);
});

test("a track list that appears late is waited for", async () => {
  const tracks = makeTracks(20);
  const loaded = new FakePlaylistPage(tracks);
  const page = new FakePlaylistPage(tracks, { page: { tracklist: false } });
  page.at(3000, () => {
    const holder = page.document.querySelector(".contentSpacing");
    holder.innerHTML = loaded.document.querySelector(".contentSpacing").innerHTML;
  });
  const { data } = await scan(page);
  assert.equal(data.tracks.length, 20);
});

test("no track list at all fails and names the selector", async () => {
  const page = new FakePlaylistPage(makeTracks(5), { page: { tracklist: false } });
  const error = await failure(page);
  assert.ok(error instanceof ScanError);
  assert.equal(error.step, "tracklist");
  assert.equal(error.selector, "tracklist");
  assert.ok(page.clock >= TIMING.tracklistWaitMs, "it waited for the page first");
  const shown = describeError(error);
  assert.equal(shown.selectorText, dom.SELECTORS.tracklist);
  assert.match(shown.message, /track list was not found/);
  assert.match(shown.debug, /Selector matches now: tracklist=0/);
});

test("no playlist name fails clearly", async () => {
  const page = new FakePlaylistPage(makeTracks(5), { page: { title: false, gridLabel: false } });
  const error = await failure(page);
  assert.equal(error.step, "name");
  assert.equal(error.selector, "playlistTitle");
  assert.match(error.message, /playlist name could not be read/);
});

test("the name falls back to the track list's label when the heading is gone", async () => {
  const { result } = await scan(new FakePlaylistPage(makeTracks(5), { name: "Daily Mix 6", page: { title: false } }));
  assert.equal(result.playlistName, "Daily Mix 6");
  assert.equal(result.filename, "daily_mix_6.json");
});

test("a page that is not a playlist is refused", async () => {
  const page = new FakePlaylistPage(makeTracks(5), { url: "https://open.spotify.com/album/ABC123" });
  assert.equal((await failure(page)).step, "page");
  const elsewhere = new FakePlaylistPage(makeTracks(5), { url: "https://example.com/playlist/ABC123" });
  assert.equal((await failure(elsewhere)).step, "page");
});

test("a track list that does not say how long it is fails rather than guessing", async () => {
  const page = new FakePlaylistPage(makeTracks(30), { page: { rowCount: false } });
  const error = await failure(page);
  assert.equal(error.step, "count");
  assert.match(error.message, /aria-rowcount/);
});

test("rows whose position is not a number fail rather than being guessed at", async () => {
  const page = new FakePlaylistPage(makeTracks(30), { row: { rowIndex: "third" } });
  const error = await failure(page);
  assert.equal(error.step, "rows");
  assert.equal(error.selector, "row");
  assert.match(error.message, /aria-rowindex/);
});

test("rows that no longer carry a position at all fail and name the selector", async () => {
  const page = new FakePlaylistPage(makeTracks(30), { row: { rowIndex: false } });
  const error = await failure(page);
  assert.equal(error.step, "stalled");
  assert.equal(error.selector, "row");
  assert.match(error.message, /Row 1 of 30 never appeared/);
  assert.match(describeError(error).debug, /rows in the page: none/);
});

test("an empty playlist fails with a plain message", async () => {
  const error = await failure(new FakePlaylistPage([]));
  assert.equal(error.step, "empty");
  assert.match(error.message, /no songs/);
});

test("a playlist that ends before its stated length fails instead of exporting short", async () => {
  const page = new FakePlaylistPage(makeTracks(50), { page: { rowCount: 61 }, startTop: 400 });
  const error = await failure(page);
  assert.equal(error.step, "stalled");
  assert.match(error.message, /Row 51 of 60 never appeared/);
  assert.match(error.message, /50 rows were read/);
  assert.equal(page.scroller.scrollTop, 400, "the scroll position is put back after a failure too");
  assert.match(error.debug, /stalled waiting for row 51/);
});

test("a playlist with more rows than it says fails instead of exporting short", async () => {
  const page = new FakePlaylistPage(makeTracks(50), { page: { rowCount: 41 }, startTop: 150 });
  const error = await failure(page);
  assert.equal(error.step, "count");
  assert.equal(error.selector, "tracklist");
  assert.match(error.message, /says it has 40 songs, but row \d+ is on the page/);
  assert.equal(page.scroller.scrollTop, 150);
});

test("rows that never load fail instead of exporting short", async () => {
  const page = new FakePlaylistPage(makeTracks(120), { neverLoadFrom: 77 });
  const error = await failure(page);
  assert.equal(error.step, "stalled");
  assert.match(error.message, /Row 77 of 120 never appeared/);
  assert.equal(page.scroller.scrollTop, 0);
});

test("a first row that never shows fails instead of exporting from the second", async () => {
  const page = new FakePlaylistPage(makeTracks(40), { page: { header: false, rowCount: 41 } });
  const error = await failure(page);
  assert.equal(error.step, "stalled");
  assert.match(error.message, /Row 1 of 41 never appeared/);
});

test("when no row can be read the failure names the selector and shows a row", async () => {
  const page = new FakePlaylistPage(makeTracks(30), { row: { artistHref: () => "/creator/XYZ" } });
  const error = await failure(page);
  assert.equal(error.step, "rows");
  assert.equal(error.selector, "artistLink");
  assert.match(error.message, /None of the 30 rows could be read: no artist links\./);
  const shown = describeError(error);
  assert.equal(shown.selectorText, dom.SELECTORS.artistLink);
  assert.match(shown.debug, /Markup of row 1, which could not be read:/);
  assert.match(shown.debug, /href="\/creator\/XYZ"/);
  assert.match(shown.debug, /trackLink=\d\d  artistLink=0  /, "counted inside the track list, where the player bar's links do not count");
});

test("a playlist that changes while it is read fails instead of mixing two versions", async () => {
  const tracks = makeTracks(120);
  const page = new FakePlaylistPage(tracks, { startTop: 0 });
  page.at(300, () => {
    page.tracks = makeTracks(120, "Tomorrow's Song").map((track, i) => ({ ...track, id: `NEW${String(i).padStart(19, "0")}` }));
  });
  const error = await failure(page);
  assert.equal(error.step, "changed");
  assert.match(error.message, /changed while it was being read/);
  assert.equal(page.scroller.scrollTop, 0);
});

test("a track list that is replaced while it is read fails", async () => {
  const page = new FakePlaylistPage(makeTracks(200));
  page.at(400, () => {
    page.document.querySelector('[data-testid="playlist-tracklist"]').remove();
    return false;
  });
  const error = await failure(page);
  assert.equal(error.step, "changed");
});

test("stopping part-way ends the scan and puts the scroll position back", async () => {
  const page = new FakePlaylistPage(makeTracks(400), { startTop: 250 });
  let stopped = false;
  page.at(500, () => {
    stopped = true;
    return false;
  });
  const error = await failure(page, { isCancelled: () => stopped });
  assert.equal(error.step, "cancelled");
  assert.ok(page.clock < 1500, `stopped at ${page.clock} ms`);
  assert.equal(page.scroller.scrollTop, 250);
});

test("the scan touches nothing on the page but the scroll position", async () => {
  const page = new FakePlaylistPage(makeTracks(150), { startTop: 300 });
  const before = page.document.documentElement.outerHTML;
  const drawsBefore = page.draws;
  const view = page.dom.window;
  const calls = [];
  for (const [owner, name] of [
    [view.HTMLElement.prototype, "click"],
    [view.HTMLElement.prototype, "focus"],
    [view.EventTarget.prototype, "dispatchEvent"],
    [view.Element.prototype, "setAttribute"],
    [view.Element.prototype, "remove"],
    [view.Node.prototype, "appendChild"],
  ]) {
    const original = owner[name];
    owner[name] = function (...args) {
      if (!page.drawing) calls.push(name);
      return original.apply(this, args);
    };
  }
  const draw = page.draw.bind(page);
  page.draw = () => {
    page.drawing = true;
    try {
      draw();
    } finally {
      page.drawing = false;
    }
  };
  await scan(page);
  await page.sleep(1000); // let the page redraw at the restored position
  assert.deepEqual(calls, []);
  assert.ok(page.draws > drawsBefore);
  assert.equal(page.document.documentElement.outerHTML, before, "the page is as it was found");
});

test("debug text records the steps, and only the last few hundred", async () => {
  const { result } = await scan(new FakePlaylistPage(makeTracks(60), { name: "Daily Mix 2" }), { version: "9.9.9" });
  assert.match(result.debug, /^Daily Mix Sync extension 9\.9\.9\nOutcome: exported 60 tracks\nPage: https:\/\/open\.spotify\.com\/playlist\//);
  assert.match(result.debug, /playlist "Daily Mix 2"; aria-rowcount=61/);
  assert.match(result.debug, /scrolling element found/);
  assert.match(result.debug, /60 of 60 read/);
  assert.match(result.debug, /scroll position put back to 0/);
  assert.match(result.debug, /Markup of row 1:/);
});

test("with the debug flag each step is also written to the console", async () => {
  const original = console.log;
  const lines = [];
  console.log = (...args) => lines.push(args.join(" "));
  let withFlag;
  try {
    await scan(new FakePlaylistPage(makeTracks(10)), { debug: true });
    withFlag = lines.length;
    await scan(new FakePlaylistPage(makeTracks(10)));
  } finally {
    console.log = original;
  }
  assert.ok(withFlag > 2);
  assert.ok(lines.every((line) => line.startsWith("[Daily Mix Sync] ")));
  assert.equal(lines.length, withFlag, "the run without the flag logged nothing");
});

test("a needed row that is on the page but never gets recorded ends in a failure, not a spin", async () => {
  const format = require("../../extension/export_format.js");
  const original = format.mergeRows;
  // Stand in for any future fault that leaves a drawn row out: refuse row 7.
  format.mergeRows = (collected, rows) => original(collected, rows.filter((row) => row.position !== 7));
  const page = new FakePlaylistPage(makeTracks(40));
  let sleeps = 0;
  const sleep = page.sleep.bind(page);
  try {
    const error = await failure(page, {
      sleep: (ms) => {
        sleeps += 1;
        return sleep(ms);
      },
    });
    assert.equal(error.step, "stalled");
    assert.match(error.message, /Row 7 of 40 never appeared/);
    assert.ok(page.clock <= TIMING.stallMs + 2 * TIMING.settleMs + 1000, `gave up after ${page.clock} ms`);
    assert.ok(sleeps > 20, "it kept pausing while it waited");
  } finally {
    format.mergeRows = original;
  }
});

test("the test clock stops a scan that would never end", async () => {
  const page = new FakePlaylistPage(makeTracks(10));
  await assert.rejects(
    (async () => {
      for (;;) await page.sleep(1000);
    })(),
    /ten simulated minutes/
  );
  const env = staticEnv(staticPage("Daily Mix 1", makeTracks(3)));
  await assert.rejects(
    (async () => {
      for (;;) await env.sleep(1000);
    })(),
    /ten simulated minutes/
  );
});

test("the test clock stops a scan that spins without pausing", () => {
  const page = new FakePlaylistPage(makeTracks(10));
  const env = page.env();
  assert.throws(() => {
    for (;;) env.now();
  }, /100,000 times without pausing/);
  const fixed = staticEnv(staticPage("Daily Mix 1", makeTracks(3)));
  assert.throws(() => {
    for (;;) fixed.now();
  }, /100,000 times without pausing/);
});

test("describeError keeps unexpected errors readable", () => {
  const shown = describeError(new TypeError("x is not a function"));
  assert.deepEqual(shown, { step: "unexpected", message: "x is not a function", selector: null, selectorText: null, debug: "" });
});

test("an unexpected error still carries debug text", async () => {
  const page = new FakePlaylistPage(makeTracks(10));
  const error = await failure(page, { timestamp: () => { throw new Error("clock broke"); } });
  assert.equal(error.message, "clock broke");
  assert.match(error.debug, /Outcome: failed at step "unexpected"/);
  assert.equal(page.scroller.scrollTop, 0);
});

test("a static page with every row present needs no scrolling element", async () => {
  const doc = staticPage("Daily Mix 5", makeTracks(9));
  doc.querySelector("[data-overlayscrollbars-viewport]").removeAttribute("style");
  const result = await scanPlaylist(staticEnv(doc));
  assert.equal(result.trackCount, 9);
  assert.match(result.debug, /no scrolling element found/);
});
