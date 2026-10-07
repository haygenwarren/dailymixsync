"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const format = require("../../extension/export_format.js");

const row = (position, fields = {}) => ({
  position,
  title: `Song ${position}`,
  artists: ["Someone"],
  album: "An Album",
  durationMs: 200000,
  trackId: `ID${position}`,
  problem: null,
  ...fields,
});

test("parseDuration reads minutes and seconds", () => {
  assert.equal(format.parseDuration("3:42"), 222000);
  assert.equal(format.parseDuration("0:07"), 7000);
  assert.equal(format.parseDuration("12:00"), 720000);
  assert.equal(format.parseDuration("59:59"), 3599000);
});

test("parseDuration reads hours", () => {
  assert.equal(format.parseDuration("1:03:15"), 3795000);
  assert.equal(format.parseDuration("10:00:00"), 36000000);
  assert.equal(format.parseDuration("1:00:00"), 3600000);
});

test("parseDuration ignores surrounding space and invisible direction marks", () => {
  assert.equal(format.parseDuration("  3:42\n"), 222000);
  assert.equal(format.parseDuration("‎3:42‏"), 222000);
  assert.equal(format.parseDuration("⁦3:42⁩"), 222000);
});

test("parseDuration returns null rather than guessing", () => {
  for (const text of ["", "   ", "3", "342", "3:4", "3:60", "1:60:00", "1:2:03", "3:42 min", "-3:42",
    "3.42", "three", "2 weeks ago", "0:00", "0:00:00", "1:03:15:00", "3:42:", ":42", "3::42"]) {
    assert.equal(format.parseDuration(text), null, JSON.stringify(text));
  }
});

test("parseDuration returns null for anything that is not text", () => {
  for (const value of [null, undefined, 222, 3.42, {}, [], true]) {
    assert.equal(format.parseDuration(value), null);
  }
});

test("parseDuration always gives whole milliseconds", () => {
  for (const text of ["3:42", "1:03:15", "0:01"]) {
    assert.ok(Number.isInteger(format.parseDuration(text)));
  }
});

test("trackIdFromHref reads the ID from the links Spotify uses", () => {
  assert.equal(format.trackIdFromHref("/track/ABC123"), "ABC123");
  assert.equal(format.trackIdFromHref("/track/ABC123?si=xyz&utm=1"), "ABC123");
  assert.equal(format.trackIdFromHref("/track/ABC123#fragment"), "ABC123");
  assert.equal(format.trackIdFromHref("/track/ABC123/"), "ABC123");
  assert.equal(format.trackIdFromHref("https://open.spotify.com/track/ABC123?si=xyz"), "ABC123");
  assert.equal(format.trackIdFromHref("/intl-de/track/ABC123"), "ABC123");
  assert.equal(format.trackIdFromHref("https://open.spotify.com/intl-pt-br/track/ABC123?si=1"), "ABC123");
  assert.equal(format.trackIdFromHref("spotify:track:ABC123"), "ABC123");
  assert.equal(format.trackIdFromHref("  /track/11hcBLPtbMp4aQI6zGQLub  "), "11hcBLPtbMp4aQI6zGQLub");
});

test("trackIdFromHref never invents an ID", () => {
  for (const href of ["", "/", "/track/", "/track", "/album/ABC123", "/artist/ABC123", "/episode/ABC123",
    "/playlist/ABC123", "/album/ABC123/track/DEF456", "/track/ABC-123", "/track/ABC123/extra",
    "https://example.com/track/ABC123", "http://open.spotify.com/track/ABC123",
    "https://open.spotify.com.evil.example/track/ABC123", "spotify:album:ABC123", "spotify:track:",
    "javascript:alert(1)", "not a link"]) {
    assert.equal(format.trackIdFromHref(href), null, JSON.stringify(href));
  }
  for (const value of [null, undefined, 42, {}]) assert.equal(format.trackIdFromHref(value), null);
});

test("canonicalTrackUrl is the plain track address", () => {
  assert.equal(format.canonicalTrackUrl("ABC123"), "https://open.spotify.com/track/ABC123");
});

test("a track link with tracking parameters comes out canonical", () => {
  const id = format.trackIdFromHref("https://open.spotify.com/intl-de/track/ABC123?si=xyz");
  assert.equal(format.canonicalTrackUrl(id), "https://open.spotify.com/track/ABC123");
});

test("pageKind tells a playlist from the rest of Spotify and from other sites", () => {
  assert.equal(format.pageKind("https://open.spotify.com/playlist/37i9dQZF1E35"), "playlist");
  assert.equal(format.pageKind("https://open.spotify.com/playlist/37i9dQZF1E35?si=abc"), "playlist");
  assert.equal(format.pageKind("https://open.spotify.com/intl-de/playlist/37i9dQZF1E35"), "playlist");
  assert.equal(format.pageKind("https://open.spotify.com/"), "spotify");
  assert.equal(format.pageKind("https://open.spotify.com/album/ABC"), "spotify");
  assert.equal(format.pageKind("https://open.spotify.com/collection/tracks"), "spotify");
  assert.equal(format.pageKind("https://open.spotify.com/playlist/"), "spotify");
  assert.equal(format.pageKind("https://example.com/playlist/ABC"), "elsewhere");
  assert.equal(format.pageKind("https://www.spotify.com/playlist/ABC"), "elsewhere");
  assert.equal(format.pageKind("chrome://extensions"), "elsewhere");
  assert.equal(format.pageKind(""), "elsewhere");
  assert.equal(format.pageKind(undefined), "elsewhere");
  assert.equal(format.pageKind(null), "elsewhere");
});

test("playlistIdFromUrl reads the playlist ID", () => {
  assert.equal(format.playlistIdFromUrl("https://open.spotify.com/playlist/37i9dQZF1E35?si=1"), "37i9dQZF1E35");
  assert.equal(format.playlistIdFromUrl("https://open.spotify.com/track/ABC"), null);
});

test("exportFilename turns a playlist name into a plain file name", () => {
  assert.equal(format.exportFilename("Daily Mix 1"), "daily_mix_1.json");
  assert.equal(format.exportFilename("Daily Mix 12"), "daily_mix_12.json");
  assert.equal(format.exportFilename("  Daily   Mix 3  "), "daily_mix_3.json");
  assert.equal(format.exportFilename("Today’s Top Hits"), "todays_top_hits.json");
  assert.equal(format.exportFilename("Rock & Roll: The 70's!"), "rock_roll_the_70s.json");
});

test("exportFilename removes everything a file system could object to", () => {
  const name = format.exportFilename('../..\\etc/pass:wd*?"<>|\u0000\n.hidden');
  assert.equal(name, "etc_pass_wd_hidden.json");
  assert.doesNotMatch(name, /[\\/:*?"<>|\s\u0000-\u001f]/);
  assert.doesNotMatch(format.exportFilename("..."), /^\./);
});

test("exportFilename keeps letters and digits of other scripts", () => {
  assert.equal(format.exportFilename("Café Français"), "café_français.json");
  assert.equal(format.exportFilename("夜のプレイリスト 2"), "夜のプレイリスト_2.json");
  assert.equal(format.exportFilename("हिंदी मिक्स"), "हिंदी_मिक्स.json");
});

test("exportFilename falls back when nothing usable is left", () => {
  for (const name of ["", "   ", "!!!", "🎵🎵", null, undefined]) {
    assert.equal(format.exportFilename(name), "spotify_playlist.json", JSON.stringify(name));
  }
});

test("exportFilename keeps names to a sensible length", () => {
  const name = format.exportFilename("word ".repeat(100));
  assert.ok(name.length <= 85, name);
  assert.match(name, /^word(_word)*\.json$/);
});

test("mergeRows stores a row once however often it is read", () => {
  const collected = new Map();
  assert.deepEqual(format.mergeRows(collected, [row(1), row(2), row(3)]), { added: 3, conflicts: [] });
  assert.deepEqual(format.mergeRows(collected, [row(2), row(3), row(4)]), { added: 1, conflicts: [] });
  assert.deepEqual(format.mergeRows(collected, [row(1), row(2), row(3), row(4)]), { added: 0, conflicts: [] });
  assert.deepEqual([...collected.keys()], [1, 2, 3, 4]);
});

test("mergeRows keeps a song that is in the playlist twice", () => {
  const collected = new Map();
  const merged = format.mergeRows(collected, [row(1, { trackId: "SAME" }), row(2), row(3, { trackId: "SAME" })]);
  assert.equal(merged.added, 3);
  assert.equal(collected.get(1).trackId, "SAME");
  assert.equal(collected.get(3).trackId, "SAME");
});

test("mergeRows reports a position that now holds a different track", () => {
  const collected = new Map();
  format.mergeRows(collected, [row(1), row(2)]);
  const merged = format.mergeRows(collected, [row(2, { trackId: "OTHER" }), row(3)]);
  assert.deepEqual(merged, { added: 1, conflicts: [2] });
  assert.equal(collected.get(2).trackId, "ID2", "the first reading is kept");
});

test("mergeRows compares rows without an ID by title and artists", () => {
  const collected = new Map();
  format.mergeRows(collected, [row(1, { trackId: null, title: "A", artists: ["X", "Y"] })]);
  assert.deepEqual(format.mergeRows(collected, [row(1, { trackId: null, title: "A", artists: ["X", "Y"] })]).conflicts, []);
  assert.deepEqual(format.mergeRows(collected, [row(1, { trackId: null, title: "A", artists: ["X"] })]).conflicts, [1]);
  assert.deepEqual(format.mergeRows(collected, [row(1, { trackId: "NOWHASID", title: "A", artists: ["X", "Y"] })]).conflicts, [1]);
});

test("mergeRows replaces a row that could not be read with a later, readable reading", () => {
  const collected = new Map();
  format.mergeRows(collected, [row(1, { problem: { reason: "not drawn yet" }, trackId: null })]);
  const merged = format.mergeRows(collected, [row(1)]);
  assert.deepEqual(merged, { added: 0, conflicts: [] });
  assert.equal(collected.get(1).problem, null);
  format.mergeRows(collected, [row(1, { problem: { reason: "gone again" }, trackId: null })]);
  assert.equal(collected.get(1).problem, null, "a readable row is not replaced by an unreadable one");
});

test("firstMissing and missingPositions find the gaps", () => {
  const collected = new Map([[1, row(1)], [2, row(2)], [5, row(5)]]);
  assert.equal(format.firstMissing(collected, 5), 3);
  assert.deepEqual(format.missingPositions(collected, 6), [3, 4, 6]);
  assert.equal(format.firstMissing(collected, 2), null);
  assert.equal(format.firstMissing(new Map(), 0), null);
  assert.equal(format.firstMissing(new Map(), 3), 1);
  assert.deepEqual(format.missingPositions(collected, 2), []);
});

test("formatRanges writes runs of numbers compactly", () => {
  assert.equal(format.formatRanges([3, 4, 5, 9]), "3–5, 9");
  assert.equal(format.formatRanges([9, 3, 5, 4]), "3–5, 9");
  assert.equal(format.formatRanges([1]), "1");
  assert.equal(format.formatRanges([1, 2]), "1–2");
  assert.equal(format.formatRanges([1, 3, 5]), "1, 3, 5");
  assert.equal(format.formatRanges([]), "");
});

test("trackEntry writes the fields the Python importer reads", () => {
  assert.deepEqual(
    format.trackEntry(row(1, { title: "Mr. Brightside", artists: ["The Killers"], album: "Hot Fuss", durationMs: 222000, trackId: "ABC123" })),
    {
      title: "Mr. Brightside",
      artist: "The Killers",
      album: "Hot Fuss",
      duration_ms: 222000,
      spotify_track_id: "ABC123",
      spotify_url: "https://open.spotify.com/track/ABC123",
    }
  );
});

test("trackEntry joins several artists with a comma and a space", () => {
  assert.equal(format.trackEntry(row(1, { artists: ["Artist One", "Artist Two"] })).artist, "Artist One, Artist Two");
  assert.equal(format.trackEntry(row(1, { artists: ["A", "B", "C"] })).artist, "A, B, C");
});

test("trackEntry leaves out what Spotify did not show", () => {
  const entry = format.trackEntry(row(1, { album: "", durationMs: null, trackId: null }));
  assert.deepEqual(entry, { title: "Song 1", artist: "Someone" });
  assert.ok(!("spotify_url" in entry) && !("spotify_track_id" in entry));
});

test("trackEntry does not write a duration that is not a positive whole number", () => {
  for (const durationMs of [0, -5, 1.5, NaN, "222000", undefined]) {
    assert.ok(!("duration_ms" in format.trackEntry(row(1, { durationMs }))), String(durationMs));
  }
});

test("trackEntry passes titles and artists through untouched", () => {
  const entry = format.trackEntry(
    row(1, { title: "Déjà Vu (feat. JAY-Z)  - Remastered 2011", artists: ["Beyoncé", "Tyler, The Creator"], album: "( )" })
  );
  assert.equal(entry.title, "Déjà Vu (feat. JAY-Z)  - Remastered 2011");
  assert.equal(entry.artist, "Beyoncé, Tyler, The Creator");
  assert.equal(entry.album, "( )");
});

test("buildExport writes the playlist in order, whatever order the rows came in", () => {
  const data = format.buildExport({
    playlistName: "Daily Mix 1",
    sourceUrl: "https://open.spotify.com/playlist/XYZ",
    exportedAt: "2026-10-07T12:00:00.000Z",
    rows: [row(3), row(1), row(2)],
  });
  assert.deepEqual(Object.keys(data), ["playlist_name", "source_url", "exported_at", "tracks"]);
  assert.equal(data.playlist_name, "Daily Mix 1");
  assert.deepEqual(data.tracks.map((track) => track.title), ["Song 1", "Song 2", "Song 3"]);
});

test("buildExport does not change the rows it is given", () => {
  const rows = [row(2), row(1)];
  format.buildExport({ playlistName: "X", rows });
  assert.deepEqual(rows.map((r) => r.position), [2, 1]);
});

test("buildExport leaves out the optional top-level fields when they are unknown", () => {
  const data = format.buildExport({ playlistName: "Daily Mix 1", rows: [row(1)] });
  assert.deepEqual(Object.keys(data), ["playlist_name", "tracks"]);
});

test("serializeExport writes readable JSON that reads back the same", () => {
  const data = format.buildExport({
    playlistName: "Mix “Ünïcödé” 夜",
    rows: [row(1, { title: 'Say "Hi" \\ / \n 🎵', artists: ["Sigur Rós"] })],
  });
  const text = format.serializeExport(data);
  assert.ok(text.endsWith("}\n"));
  assert.ok(text.includes('\n  "tracks": [\n'), "indented with two spaces");
  assert.ok(text.includes("Sigur Rós") && text.includes("夜") && text.includes("🎵"), "not escaped to \\u codes");
  assert.deepEqual(JSON.parse(text), data);
});
