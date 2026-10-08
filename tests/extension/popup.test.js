"use strict";

// The popup, run for real: popup.html and popup.js in jsdom, with a stand-in for the
// parts of Chrome's extension API they use. No browser.
//
// What matters here: the states it shows, that an export is saved exactly as it
// arrives from the page, and what it tells the user to do next.

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const EXTENSION = path.join(__dirname, "..", "..", "extension");
const read = (name) => fs.readFileSync(path.join(EXTENSION, name), "utf8");

const PLAYLIST = "https://open.spotify.com/playlist/37i9dQZF1E35FIXTURE0001";
const NEXT_COMMAND = "python -m daily_mix_sync sync-downloads";
const EXPORT_JSON = '{\n  "playlist_name": "Daily Mix 1",\n  "tracks": [\n    {"title": "Déjà Vu", "artist": "Beyoncé"}\n  ]\n}\n';

function finished(overrides = {}) {
  return {
    playlistName: "Daily Mix 1",
    filename: "daily_mix_1.json",
    json: EXPORT_JSON,
    trackCount: 50,
    rowCount: 50,
    first: "Mr. Brightside — The Killers",
    last: "Dreams — Fleetwood Mac",
    warnings: [],
    debug: "Daily Mix Sync extension 0.1.0\nOutcome: exported 50 tracks",
    ...overrides,
  };
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));
// What the popup hands to Chrome is made inside the simulated page; this turns it
// into ordinary objects of this program, so that they can be compared.
const plain = (value) => JSON.parse(JSON.stringify(value));

// Open the popup as if its button had been clicked on a tab showing `tabUrl`.
async function openPopup(tabUrl, options = {}) {
  const dom = new JSDOM(read("popup.html"), {
    runScripts: "outside-only",
    url: "chrome-extension://abcdefghijklmnop/popup.html",
  });
  const { window } = dom;
  const seen = { queries: [], injected: [], connected: [], posted: [], downloads: [], disconnects: 0, copied: [] };
  const port = {
    listeners: [],
    disconnectListeners: [],
    onMessage: { addListener: (fn) => port.listeners.push(fn) },
    onDisconnect: { addListener: (fn) => port.disconnectListeners.push(fn) },
    postMessage: (message) => seen.posted.push(plain(message)),
    disconnect: () => {
      seen.disconnects += 1;
    },
  };
  window.chrome = {
    runtime: { lastError: undefined },
    tabs: {
      query: async (query) => {
        seen.queries.push(plain(query));
        return [{ id: 7, url: tabUrl }];
      },
      connect: (tabId, details) => {
        seen.connected.push({ tabId, ...plain(details) });
        return port;
      },
    },
    scripting: {
      executeScript: async (details) => {
        if (options.injectionFails) throw new Error("Cannot access contents of the page");
        seen.injected.push(plain(details));
      },
    },
  };

  // Saving a file: a Blob, an object URL for it, and a click on a link to that URL.
  const blobs = new Map();
  const RealBlob = window.Blob;
  window.Blob = class extends RealBlob {
    constructor(parts, settings) {
      super(parts, settings);
      this.parts = parts;
      this.settings = settings;
    }
  };
  window.URL.createObjectURL = (blob) => {
    const url = `blob:fake/${blobs.size + 1}`;
    blobs.set(url, blob);
    return url;
  };
  window.URL.revokeObjectURL = () => {};
  window.HTMLAnchorElement.prototype.click = function () {
    const blob = blobs.get(this.href);
    seen.downloads.push({ filename: this.download, content: blob.parts.join(""), type: blob.settings.type });
  };
  Object.defineProperty(window.navigator, "clipboard", {
    configurable: true,
    value: {
      writeText: async (text) => {
        if (options.clipboardFails) throw new Error("denied");
        seen.copied.push(text);
      },
    },
  });

  window.eval(read("export_format.js"));
  window.eval(read("popup.js"));
  await settle();

  const element = (id) => window.document.getElementById(id);
  return {
    seen,
    document: window.document,
    state: () => window.document.body.dataset.state,
    text: (id) => element(id).textContent,
    shown: (id) => !element(id).hidden,
    element,
    click: async (id) => {
      element(id).click();
      await settle();
    },
    fromPage: async (message) => {
      for (const listener of port.listeners) listener(message);
      await settle();
    },
    pageGoesAway: async () => {
      for (const listener of port.disconnectListeners) listener();
      await settle();
    },
    close: () => window.close(), // also drops the timer that frees the object URL
  };
}

// --- which tab it is on -----------------------------------------------------------------

test("on a tab that is not Spotify it says so and offers nothing", async () => {
  for (const url of ["https://example.com/", "chrome://extensions", undefined, "https://www.spotify.com/us/"]) {
    const popup = await openPopup(url);
    assert.equal(popup.state(), "elsewhere", String(url));
    assert.equal(popup.text("headline"), "This tab is not Spotify.");
    assert.equal(popup.text("detail"), "Open a Daily Mix on open.spotify.com, then click this button again.");
    for (const id of ["export", "download", "copy-debug", "command", "debug-option", "progress"]) {
      assert.equal(popup.shown(id), false, id);
    }
    assert.deepEqual(popup.seen.injected, []);
    popup.close();
  }
});

test("on a Spotify page that is not a playlist it says so and offers nothing", async () => {
  for (const url of ["https://open.spotify.com/", "https://open.spotify.com/album/ABC", "https://open.spotify.com/collection/tracks"]) {
    const popup = await openPopup(url);
    assert.equal(popup.state(), "spotify", url);
    assert.equal(popup.text("headline"), "This Spotify page is not a playlist.");
    assert.equal(popup.shown("export"), false);
    assert.equal(popup.shown("command"), false);
    popup.close();
  }
});

test("on a playlist it offers to export, having asked only about the current tab", async () => {
  const popup = await openPopup(PLAYLIST);
  assert.equal(popup.state(), "ready");
  assert.equal(popup.text("headline"), "Ready to export the playlist in this tab.");
  assert.equal(popup.shown("export"), true);
  assert.equal(popup.text("export"), "Export this playlist");
  assert.equal(popup.shown("debug-option"), true);
  assert.equal(popup.shown("command"), false, "nothing to run yet");
  assert.deepEqual(popup.seen.queries, [{ active: true, currentWindow: true }]);
  assert.deepEqual(popup.seen.injected, [], "nothing is put into the page until Export is clicked");
  popup.close();
});

// --- exporting ----------------------------------------------------------------------------

test("Export puts the three page scripts into that tab, in order, and asks for a scan", async () => {
  const popup = await openPopup(PLAYLIST);
  await popup.click("export");
  assert.equal(popup.state(), "scanning");
  assert.deepEqual(popup.seen.injected, [
    { target: { tabId: 7 }, files: ["export_format.js", "spotify_dom.js", "content.js"] },
  ]);
  assert.deepEqual(popup.seen.connected, [{ tabId: 7, name: "daily-mix-sync" }]);
  assert.deepEqual(popup.seen.posted, [{ type: "scan", debug: false }]);
  assert.equal(popup.shown("progress"), true);
  assert.equal(popup.shown("export"), false);
  assert.match(popup.text("detail"), /Keep this popup open/);
  popup.close();
});

test("the debug box asks the page to log its steps", async () => {
  const popup = await openPopup(PLAYLIST);
  popup.element("debug").checked = true;
  await popup.click("export");
  assert.deepEqual(popup.seen.posted, [{ type: "scan", debug: true }]);
  popup.close();
});

test("progress from the page is shown as it arrives", async () => {
  const popup = await openPopup(PLAYLIST);
  await popup.click("export");
  assert.equal(popup.text("headline"), "Reading tracks…");
  await popup.fromPage({ type: "progress", collected: 37, expected: 50 });
  assert.equal(popup.text("headline"), "Reading tracks… 37 of 50");
  assert.equal(popup.element("progress").max, 50);
  assert.equal(popup.element("progress").value, 37);
  assert.equal(popup.seen.downloads.length, 0);
  popup.close();
});

test("a finished export is saved exactly as the page sent it", async () => {
  const popup = await openPopup(PLAYLIST);
  await popup.click("export");
  await popup.fromPage({ type: "progress", collected: 50, expected: 50 });
  await popup.fromPage({ type: "done", result: finished() });
  assert.equal(popup.state(), "done");
  assert.deepEqual(popup.seen.downloads, [
    { filename: "daily_mix_1.json", content: EXPORT_JSON, type: "application/json" },
  ]);
  assert.equal(popup.seen.disconnects, 1, "the connection to the page is closed");
  popup.close();
});

test("after an export it says what was saved and what to run next", async () => {
  const popup = await openPopup(PLAYLIST);
  await popup.click("export");
  await popup.fromPage({ type: "done", result: finished() });
  assert.equal(popup.text("headline"), "Saved daily_mix_1.json");
  assert.equal(
    popup.text("detail"),
    "50 tracks from “Daily Mix 1”. First: Mr. Brightside — The Killers. Last: Dreams — Fleetwood Mac."
  );
  assert.equal(popup.text("lookup"), "Export your other Daily Mixes, then run:");
  assert.equal(popup.shown("command"), true);
  assert.equal(popup.text("command"), NEXT_COMMAND);
  assert.equal(popup.shown("download"), true);
  assert.equal(popup.text("export"), "Export again");
  assert.equal(popup.shown("progress"), false);
  popup.close();
});

test("the next step is text to copy, not something the popup can run", async () => {
  const popup = await openPopup(PLAYLIST);
  await popup.click("export");
  await popup.fromPage({ type: "done", result: finished() });
  const command = popup.element("command");
  assert.equal(command.tagName, "CODE");
  assert.equal(command.childElementCount, 0, "plain text: no link, no button inside");
  assert.equal(command.closest("a, button"), null);
  assert.deepEqual(
    [...popup.document.querySelectorAll("button")].map((button) => button.id),
    ["export", "download", "copy-debug"],
    "no new button"
  );
  assert.equal(popup.document.querySelectorAll("a[href], form, iframe").length, 0);
  popup.close();
});

test("nothing in the popup tells the user to move the file any more", async () => {
  const popup = await openPopup(PLAYLIST);
  await popup.click("export");
  await popup.fromPage({ type: "done", result: finished({ warnings: ["2 of 52 rows left out: row 5 (a podcast episode, not a song)."] }) });
  const everything = popup.document.body.textContent;
  for (const stale of ["data/", "Move the file", "validate", "mv "]) {
    assert.ok(!everything.includes(stale), stale);
  }
  assert.ok(!read("popup.js").includes("data/"));
  assert.ok(!read("popup.html").includes("data/"));
  popup.close();
});

test("one track is one track", async () => {
  const popup = await openPopup(PLAYLIST);
  await popup.click("export");
  await popup.fromPage({ type: "done", result: finished({ trackCount: 1 }) });
  assert.match(popup.text("detail"), /^1 track from /);
  popup.close();
});

test("warnings from the page are listed", async () => {
  const warnings = ["2 of 52 rows left out: row 5 (a podcast episode, not a song).", "No album name found for any track."];
  const popup = await openPopup(PLAYLIST);
  await popup.click("export");
  await popup.fromPage({ type: "done", result: finished({ warnings }) });
  assert.equal(popup.shown("warnings"), true);
  assert.deepEqual([...popup.element("warnings").children].map((item) => item.textContent), warnings);
  popup.close();
});

test("Download again saves the same file once more", async () => {
  const popup = await openPopup(PLAYLIST);
  await popup.click("export");
  await popup.fromPage({ type: "done", result: finished() });
  await popup.click("download");
  assert.equal(popup.seen.downloads.length, 2);
  assert.deepEqual(popup.seen.downloads[1], popup.seen.downloads[0]);
  popup.close();
});

test("exporting again starts over and clears what the last export showed", async () => {
  const popup = await openPopup(PLAYLIST);
  await popup.click("export");
  await popup.fromPage({ type: "done", result: finished() });
  await popup.click("export");
  assert.equal(popup.state(), "scanning");
  assert.equal(popup.shown("command"), false);
  assert.equal(popup.shown("download"), false);
  assert.equal(popup.seen.injected.length, 2);
  await popup.fromPage({ type: "done", result: finished({ filename: "daily_mix_2.json", playlistName: "Daily Mix 2" }) });
  assert.equal(popup.text("headline"), "Saved daily_mix_2.json");
  assert.equal(popup.text("command"), NEXT_COMMAND);
  assert.equal(popup.seen.downloads.at(-1).filename, "daily_mix_2.json");
  popup.close();
});

// --- when it goes wrong ----------------------------------------------------------------------

const FAILURE = {
  step: "tracklist",
  message: "The track list was not found on this page.",
  selector: "tracklist",
  selectorText: '[data-testid="playlist-tracklist"]',
  debug: "Daily Mix Sync extension 0.1.0\nOutcome: failed at step \"tracklist\"",
};

test("a failure says what went wrong and which selector found nothing, and saves nothing", async () => {
  const popup = await openPopup(PLAYLIST);
  await popup.click("export");
  await popup.fromPage({ type: "failed", error: FAILURE });
  assert.equal(popup.state(), "failed");
  assert.equal(popup.text("headline"), "Export failed");
  assert.equal(popup.text("detail"), "The track list was not found on this page.");
  assert.equal(popup.text("lookup"), 'Looked for “tracklist” in spotify_dom.js: [data-testid="playlist-tracklist"]');
  assert.equal(popup.shown("command"), false, "there is nothing to run after a failure");
  assert.equal(popup.shown("download"), false);
  assert.equal(popup.shown("export"), true);
  assert.deepEqual(popup.seen.downloads, []);
  popup.close();
});

test("Copy debug info puts the page's report on the clipboard", async () => {
  const popup = await openPopup(PLAYLIST);
  await popup.click("export");
  await popup.fromPage({ type: "failed", error: FAILURE });
  assert.equal(popup.shown("copy-debug"), true);
  await popup.click("copy-debug");
  assert.deepEqual(popup.seen.copied, [FAILURE.debug]);
  assert.equal(popup.text("copy-debug"), "Copied");
  popup.close();
});

test("debug info can be copied after a successful export too", async () => {
  const popup = await openPopup(PLAYLIST);
  await popup.click("export");
  await popup.fromPage({ type: "done", result: finished() });
  await popup.click("copy-debug");
  assert.deepEqual(popup.seen.copied, [finished().debug]);
  popup.close();
});

test("a clipboard that refuses is said plainly", async () => {
  const popup = await openPopup(PLAYLIST, { clipboardFails: true });
  await popup.click("export");
  await popup.fromPage({ type: "failed", error: FAILURE });
  await popup.click("copy-debug");
  assert.equal(popup.text("copy-debug"), "Could not copy");
  popup.close();
});

test("a tab that cannot be read is reported without connecting to it", async () => {
  const popup = await openPopup(PLAYLIST, { injectionFails: true });
  await popup.click("export");
  assert.equal(popup.state(), "failed");
  assert.equal(popup.text("detail"), "The Spotify tab could not be read: Cannot access contents of the page");
  assert.deepEqual(popup.seen.connected, []);
  assert.equal(popup.shown("copy-debug"), false, "there is no report to copy");
  popup.close();
});

test("a page that goes away mid-scan is reported", async () => {
  const popup = await openPopup(PLAYLIST);
  await popup.click("export");
  await popup.pageGoesAway();
  assert.equal(popup.state(), "failed");
  assert.match(popup.text("detail"), /stopped answering/);
  assert.deepEqual(popup.seen.downloads, []);
  popup.close();
});

test("the page closing its side after a finished export changes nothing", async () => {
  const popup = await openPopup(PLAYLIST);
  await popup.click("export");
  await popup.fromPage({ type: "done", result: finished() });
  await popup.pageGoesAway();
  assert.equal(popup.state(), "done");
  assert.equal(popup.text("headline"), "Saved daily_mix_1.json");
  popup.close();
});
