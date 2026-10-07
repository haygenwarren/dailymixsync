"use strict";

// Row reading, against fixtures/playlist_page.html: a whole playlist page written the
// way Spotify writes it, with thirteen rows chosen to be awkward.

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

require("../../extension/export_format.js");
const dom = require("../../extension/spotify_dom.js");
const { PLAYLIST_URL, staticPage, rowHtml } = require("./helpers/spotify_page.js");

const FIXTURE = fs.readFileSync(path.join(__dirname, "fixtures", "playlist_page.html"), "utf8");

function fixture() {
  return new JSDOM(FIXTURE, { url: PLAYLIST_URL }).window.document;
}

function readAll(doc) {
  const tracklist = dom.findTracklist(doc);
  const { headerRows } = dom.tracklistInfo(tracklist);
  return dom.renderedRows(tracklist).map((element) => dom.readRow(element, headerRows));
}

// The row at a playlist position in the fixture.
function fixtureRow(position) {
  return readAll(fixture()).find((row) => row.position === position);
}

// One row built from a track description, read back.
function readOne(track, options) {
  const doc = staticPage("Daily Mix 1", [track], options);
  return readAll(doc)[0];
}

test("the track list is found by its test id, not by its position on the page", () => {
  const doc = fixture();
  const tracklist = dom.findTracklist(doc);
  assert.equal(tracklist.getAttribute("data-testid"), "playlist-tracklist");
  assert.equal(doc.querySelectorAll('[role="grid"]').length, 2, "the fixture also has the library's grid");
});

test("a page with no track list gives null", () => {
  assert.equal(dom.findTracklist(staticPage("Daily Mix 1", [], { tracklist: false })), null);
});

test("the playlist name comes from the page heading", () => {
  const doc = fixture();
  assert.equal(dom.readPlaylistName(doc, dom.findTracklist(doc)), "Daily Mix 1");
});

test("the playlist name is not taken from another heading on the page", () => {
  const doc = fixture();
  assert.equal(doc.querySelector("h1").textContent, "Your Library", "the first h1 is the sidebar's");
  assert.notEqual(dom.readPlaylistName(doc, dom.findTracklist(doc)), "Your Library");
});

test("the playlist name falls back to the track list's label", () => {
  const doc = staticPage("Daily Mix 4", [], { title: false });
  assert.equal(dom.readPlaylistName(doc, dom.findTracklist(doc)), "Daily Mix 4");
});

test("the playlist name is empty when the page does not give one", () => {
  const doc = staticPage("Daily Mix 4", [], { title: false, gridLabel: false });
  assert.equal(dom.readPlaylistName(doc, dom.findTracklist(doc)), "");
  assert.equal(dom.readPlaylistName(staticPage("X", [], { title: false, tracklist: false }), null), "");
});

test("the playlist name is passed on as written", () => {
  for (const name of ["Daily Mix 12", "Today’s Top Hits", "Rock & Roll <3", "夜のプレイリスト", "Mix “Ünïcödé”"]) {
    const doc = staticPage(name, []);
    assert.equal(dom.readPlaylistName(doc, dom.findTracklist(doc)), name);
  }
});

test("the song count is the row count less the header row", () => {
  const info = dom.tracklistInfo(dom.findTracklist(fixture()));
  assert.deepEqual(info, { rowCount: 14, headerRows: 1, expected: 13 });
});

test("the song count is not assumed to be fifty", () => {
  for (const count of [1, 37, 50, 68, 500]) {
    const doc = staticPage("Daily Mix 1", [], { rowCount: count + 1 });
    assert.equal(dom.tracklistInfo(dom.findTracklist(doc)).expected, count);
  }
});

test("a track list with no header row counts every row as a song", () => {
  const doc = staticPage("Daily Mix 1", [{ id: "A1", title: "T", artists: ["A"] }], { header: false });
  assert.deepEqual(dom.tracklistInfo(dom.findTracklist(doc)), { rowCount: 1, headerRows: 0, expected: 1 });
});

test("a track list that does not say how many rows it has gives no count", () => {
  for (const rowCount of [false, "many", ""]) {
    const doc = staticPage("Daily Mix 1", [], { rowCount });
    assert.equal(dom.tracklistInfo(dom.findTracklist(doc)).expected, null, String(rowCount));
  }
});

test("only song rows of the track list are read", () => {
  const rows = readAll(fixture());
  assert.equal(rows.length, 13, "not the header row, not the sidebar's rows");
  assert.deepEqual(rows.map((row) => row.position), [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]);
  assert.deepEqual(dom.renderedPositions(dom.findTracklist(fixture()), 1), rows.map((row) => row.position));
});

test("a plain row gives title, artist, album, duration and ID", () => {
  assert.deepEqual(fixtureRow(1), {
    position: 1,
    title: "Mr. Brightside",
    artists: ["The Killers"],
    album: "Hot Fuss",
    durationMs: 222000,
    trackId: "FIXTURE000000000000001",
    problem: null,
  });
});

test("several artists are read as separate names, in order", () => {
  assert.deepEqual(fixtureRow(2).artists, ["Calvin Harris", "Rihanna"]);
  assert.deepEqual(fixtureRow(5).artists, ["Beyoncé", "JAY-Z"]);
  assert.deepEqual(readOne({ id: "A1", title: "T", artists: ["One", "Two", "Three", "Four"] }).artists, ["One", "Two", "Three", "Four"]);
});

test("an artist name with a comma in it stays one name", () => {
  assert.deepEqual(fixtureRow(7).artists, ["Tyler, The Creator", "Kali Uchis"]);
});

test("the explicit badge is not read as part of the title or the artist", () => {
  const row = fixtureRow(3);
  assert.equal(row.title, "HUMBLE.");
  assert.deepEqual(row.artists, ["Kendrick Lamar"]);
  assert.equal(row.album, "DAMN.");
});

test("a title that looks like a duration is still the title", () => {
  const row = fixtureRow(4);
  assert.equal(row.title, "4:33");
  assert.equal(row.durationMs, 273000);
  const other = readOne({ id: "A1", title: "4:33", artists: ["John Cage"], duration: "12:00" });
  assert.equal(other.title, "4:33");
  assert.equal(other.durationMs, 720000);
  assert.equal(readOne({ id: "A1", title: "4:33", artists: ["John Cage"] }).durationMs, null);
});

test("featured artists and accents in a title are left as they are", () => {
  assert.equal(fixtureRow(5).title, "Déjà Vu (feat. JAY-Z)");
  assert.equal(fixtureRow(5).album, "B'Day Deluxe Edition");
  assert.equal(fixtureRow(8).title, "Autobahn - 2009 Remaster");
});

test("text in other scripts is read as it is", () => {
  const row = fixtureRow(6);
  assert.equal(row.title, "夜に駆ける");
  assert.deepEqual(row.artists, ["YOASOBI"]);
});

test("a duration over an hour is read", () => {
  assert.equal(fixtureRow(8).durationMs, 3795000);
});

test("markup characters in names survive", () => {
  const row = fixtureRow(11);
  assert.equal(row.title, 'Rock & Roll <Live> "Bootleg"');
  assert.deepEqual(row.artists, ["AC/DC"]);
});

test("a row with no album and no duration is read without them", () => {
  const row = fixtureRow(11);
  assert.equal(row.album, "");
  assert.equal(row.durationMs, null);
  assert.equal(row.problem, null);
  assert.equal(row.trackId, "FIXTURE000000000000011");
});

test("two songs with the same title are two different tracks", () => {
  const first = fixtureRow(1);
  const second = fixtureRow(12);
  assert.equal(first.title, second.title);
  assert.notEqual(first.trackId, second.trackId);
  assert.deepEqual(second.artists, ["Run River North"]);
});

test("a track link with a language prefix and tracking parameters gives the plain ID", () => {
  assert.equal(fixtureRow(12).trackId, "FIXTURE000000000000012");
});

test("space around a title is dropped, nothing else", () => {
  assert.equal(fixtureRow(13).title, "Sigur 1 (Untitled)");
  assert.equal(fixtureRow(13).album, "( )");
  assert.equal(readOne({ id: "A1", title: "Two  Spaces", artists: ["A"] }).title, "Two  Spaces");
});

test("a podcast episode is reported, not exported as a song", () => {
  const row = fixtureRow(9);
  assert.equal(row.problem.selector, "trackLink");
  assert.match(row.problem.reason, /podcast episode/);
  assert.match(row.problem.text, /How Playlists Are Made/);
  assert.equal(row.trackId, null);
});

test("a row with no Spotify link is reported with its text", () => {
  const row = fixtureRow(10);
  assert.equal(row.problem.selector, "trackLink");
  assert.match(row.problem.reason, /no link to a Spotify track/);
  assert.match(row.problem.text, /Garage Demo 3/);
  assert.match(row.problem.text, /My Old Band/);
  assert.equal(row.title, "");
});

test("a track with no artist links is reported and names the selector", () => {
  const row = readOne({ id: "A1", title: "Lonely", artists: ["Nobody"] }, { artistHref: () => "/creator/XYZ" });
  assert.equal(row.problem.selector, "artistLink");
  assert.equal(row.title, "Lonely");
});

test("a track link with no text is reported", () => {
  const row = readOne({ id: "A1", title: "", artists: ["Someone"] });
  assert.equal(row.problem.selector, "trackLink");
});

test("a track link whose address is not a track gets no ID", () => {
  const row = readOne({ id: "A1", href: "/track/", title: "Odd", artists: ["Someone"] });
  assert.equal(row.trackId, null);
  assert.equal(row.problem, null);
  assert.equal(row.title, "Odd");
});

test("links outside the track list are never read as rows", () => {
  const doc = fixture();
  assert.ok(doc.querySelector('[data-testid="now-playing-bar"] a[href*="/track/"]'), "the fixture has one in the player bar");
  const ids = readAll(doc).map((row) => row.trackId);
  assert.ok(!ids.includes("NOWPLAYINGTRACK0000001"));
});

test("the player bar's clock is not read as a duration", () => {
  const row = readOne({ id: "A1", title: "No Duration", artists: ["Someone"] });
  assert.equal(row.durationMs, null);
});

test("generated class names play no part", () => {
  const stripped = FIXTURE.replace(/\sclass="[^"]*"/g, "");
  assert.ok(stripped.length < FIXTURE.length);
  const doc = new JSDOM(stripped, { url: PLAYLIST_URL }).window.document;
  assert.deepEqual(readAll(doc), readAll(fixture()));
  assert.equal(dom.readPlaylistName(doc, dom.findTracklist(doc)), "Daily Mix 1");
  assert.ok(dom.findScroller(dom.findTracklist(doc)));
});

test("no selector depends on a class name", () => {
  for (const [name, selector] of Object.entries(dom.SELECTORS)) {
    assert.doesNotMatch(selector, /\.[A-Za-z_]|\[class/, name);
  }
});

test("rows without a row index have no usable position", () => {
  const doc = new JSDOM(`<div data-testid="playlist-tracklist">${rowHtml({ id: "A1", title: "T", artists: ["A"] }, 1)}</div>`).window.document;
  doc.querySelector('[role="row"]').setAttribute("aria-rowindex", "first");
  const row = dom.readRow(doc.querySelector('[role="row"]'), 1);
  assert.ok(!Number.isInteger(row.position));
});

test("the scrolling element is the nearest ancestor that scrolls", () => {
  const doc = fixture();
  const scroller = dom.findScroller(dom.findTracklist(doc));
  assert.ok(scroller.hasAttribute("data-overlayscrollbars-viewport"));
});

test("an element set to overflow auto counts as scrolling; one that is not does not", () => {
  const build = (style) =>
    new JSDOM(`<div id="outer" style="overflow-y: scroll"><div id="inner" style="${style}"><div data-testid="playlist-tracklist"></div></div></div>`).window.document;
  assert.equal(dom.findScroller(dom.findTracklist(build("overflow-y: auto"))).id, "inner");
  assert.equal(dom.findScroller(dom.findTracklist(build("overflow-y: hidden"))).id, "outer");
  assert.equal(dom.findScroller(dom.findTracklist(build(""))).id, "outer");
});

test("a page where nothing scrolls gives null", () => {
  const doc = new JSDOM(`<div><div data-testid="playlist-tracklist"></div></div>`).window.document;
  assert.equal(dom.findScroller(dom.findTracklist(doc)), null);
});

test("selectorCounts counts rows and links inside the track list only", () => {
  const doc = fixture();
  assert.deepEqual(dom.selectorCounts(doc), {
    tracklist: 1,
    playlistTitle: 1,
    row: 14,
    headerCell: 5,
    cell: 65,
    trackLink: 11,
    artistLink: 14,
    albumLink: 10,
    episodeLink: 1,
  });
  assert.equal(doc.querySelectorAll(dom.SELECTORS.trackLink).length, 12, "one more in the player bar");
});

test("selectorCounts on a page with no track list is all zeros but the title", () => {
  const counts = dom.selectorCounts(staticPage("Daily Mix 1", [], { tracklist: false }));
  assert.equal(counts.playlistTitle, 1);
  assert.deepEqual(Object.values(counts).reduce((sum, count) => sum + count, 0), 1);
});

test("describeElement shows structure and text without class names or image addresses", () => {
  const doc = fixture();
  const out = dom.describeElement(dom.renderedRows(dom.findTracklist(doc))[2]);
  assert.match(out, /^<div role="row" aria-rowindex="4" aria-selected="false">/);
  assert.match(out, /<a draggable="false" data-testid="internal-track-link" href="\/track\/FIXTURE000000000000003"/);
  assert.match(out, /"HUMBLE\."/);
  assert.match(out, /aria-label="Explicit"/);
  assert.doesNotMatch(out, /class=|style=/);
});

test("describeElement cuts long output", () => {
  const doc = fixture();
  const out = dom.describeElement(dom.findTracklist(doc), 500);
  assert.ok(out.length < 520);
  assert.ok(out.endsWith("… (cut)"));
});

test("reading the page does not change it", () => {
  const doc = fixture();
  const before = doc.documentElement.outerHTML;
  const tracklist = dom.findTracklist(doc);
  dom.readPlaylistName(doc, tracklist);
  dom.tracklistInfo(tracklist);
  dom.findScroller(tracklist);
  readAll(doc);
  dom.describeElement(tracklist);
  assert.equal(doc.documentElement.outerHTML, before);
});
