// Live check: run the real extension, in a real Chrome, against the real Spotify site.
//
//   npm run test:live                      the default public playlists
//   npm run test:live -- URL [URL ...]     playlists of your choice
//   npm run test:live -- --keep DIR        also keep the exported files in DIR
//
// What it does: starts a hidden copy of Google Chrome with a throwaway profile, loads
// the unpacked extension from extension/, opens each playlist signed out, clicks the
// extension's button the way you would, and compares the downloaded file with what
// the page itself says it contains.
//
// What it does not do: it never touches your own Chrome profile or your Spotify
// account, and it cannot open a Daily Mix, which needs you to be signed in. It is a
// check of the extension against Spotify's current markup, on public playlists.
//
// Needs Google Chrome and a network connection. No npm packages.

import { spawn } from "node:child_process";
import { cpSync, existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const CHROME = process.env.CHROME_PATH || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const EXTENSION = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..", "..", "extension");

// Public playlists made by Spotify. Their contents change; the check compares each
// export with what the page says at that moment, so nothing here goes out of date.
const DEFAULT_PLAYLISTS = [
  "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M", // Today's Top Hits: 50 songs
  "https://open.spotify.com/playlist/37i9dQZF1DWXRqgorJj26U", // Rock Classics: 200 songs, needs scrolling
  "https://open.spotify.com/playlist/37i9dQZF1DX10zKzsJ2jva", // Viva Latino: many artists per song, accents
  "https://open.spotify.com/playlist/37i9dQZF1DX9tPFwDMOaN1", // K-Pop ON!: text in another script
];
const MISSING_PLAYLIST = "https://open.spotify.com/playlist/0000000000000000000000";

const args = process.argv.slice(2);
const keepAt = args.includes("--keep") ? resolve(args[args.indexOf("--keep") + 1] || "") : null;
const urls = args.filter((arg, i) => arg.startsWith("https://") && args[i - 1] !== "--keep");
const playlists = urls.length ? urls : DEFAULT_PLAYLISTS;

const sleep = (ms) => new Promise((done) => setTimeout(done, ms));

// --- A very small Chrome DevTools Protocol client, over a pipe ----------------------

function startChrome(profile) {
  const chrome = spawn(
    CHROME,
    [
      "--headless=new",
      `--user-data-dir=${profile}`,
      "--remote-debugging-pipe",
      "--enable-unsafe-extension-debugging", // lets this throwaway browser load extension/
      "--no-first-run",
      "--no-default-browser-check",
      "--disable-sync",
      "--mute-audio",
      "--lang=en-US",
      "--window-size=1400,900",
      "about:blank",
    ],
    { stdio: ["ignore", "ignore", "ignore", "pipe", "pipe"] }
  );
  const pending = new Map();
  let nextId = 0;
  let buffer = "";
  chrome.stdio[4].setEncoding("utf8");
  chrome.stdio[4].on("data", (chunk) => {
    buffer += chunk;
    for (let end = buffer.indexOf("\0"); end >= 0; end = buffer.indexOf("\0")) {
      const message = JSON.parse(buffer.slice(0, end));
      buffer = buffer.slice(end + 1);
      const waiting = pending.get(message.id);
      if (!waiting) continue;
      pending.delete(message.id);
      if (message.error) waiting.reject(new Error(`${waiting.method}: ${message.error.message}`));
      else waiting.resolve(message.result);
    }
  });
  let exited = null;
  chrome.on("exit", (code, signal) => {
    exited = `Chrome exited (${signal || `code ${code}`})`;
    for (const waiting of pending.values()) waiting.reject(new Error(`${waiting.method}: ${exited}`));
    pending.clear();
  });
  const send = (method, params = {}, sessionId) => {
    if (exited) return Promise.reject(new Error(`${method}: ${exited}`));
    const id = (nextId += 1);
    chrome.stdio[3].write(`${JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) })}\0`);
    return new Promise((resolve, reject) => pending.set(id, { resolve, reject, method }));
  };
  const close = async () => {
    await Promise.race([send("Browser.close").catch(() => {}), sleep(2000)]);
    chrome.kill("SIGKILL");
    await sleep(300);
  };
  return { send, close };
}

// Attach to a target and return a function that evaluates JavaScript in it.
async function attach(browser, targetId) {
  const { sessionId } = await browser.send("Target.attachToTarget", { targetId, flatten: true });
  return async (expression) => {
    const reply = await browser.send(
      "Runtime.evaluate",
      { expression, awaitPromise: true, returnByValue: true, userGesture: true },
      sessionId
    );
    if (reply.exceptionDetails) {
      throw new Error(reply.exceptionDetails.exception?.description || reply.exceptionDetails.text);
    }
    return reply.result.value;
  };
}

async function waitFor(evaluate, expression, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const value = await evaluate(expression);
    if (value || Date.now() > deadline) return value;
    await sleep(200);
  }
}

// --- Driving the page and the popup -----------------------------------------------

const SCROLLER = `(() => { let e = document.querySelector('[data-testid="playlist-tracklist"]').parentElement;
  while (e && getComputedStyle(e).overflowY !== 'scroll') e = e.parentElement; return e; })()`;

const POPUP_VIEW = `({
  state: document.body.dataset.state,
  headline: document.getElementById('headline').textContent,
  detail: document.getElementById('detail').textContent,
  lookup: document.getElementById('lookup').textContent,
  command: document.getElementById('command').hidden ? '' : document.getElementById('command').textContent,
  fits: document.documentElement.scrollWidth <= document.documentElement.clientWidth,
  warnings: [...document.querySelectorAll('#warnings li')].map((item) => item.textContent),
})`;

// Open `url` in a new tab, in front. Returns the page, and the tab that holds it: the
// extension's toolbar button belongs to the tab, which is a target of its own.
async function openTab(browser, url) {
  const { targetId } = await browser.send("Target.createTarget", { url });
  const evaluate = await attach(browser, targetId);
  for (let attempt = 0; attempt < 50; attempt += 1) {
    const all = (await browser.send("Target.getTargets", { filter: [{}] })).targetInfos;
    const page = all.find((target) => target.targetId === targetId);
    const tabs = all.filter((target) => target.type === "tab" && page && target.url === page.url);
    if (tabs.length === 1) return { targetId, tabId: tabs[0].targetId, evaluate };
    await sleep(200);
  }
  throw new Error(`could not tell which tab is showing ${url}`);
}

// Click the extension's toolbar button for a tab and return its popup.
async function openPopup(browser, extensionId, tabId) {
  await browser.send("Extensions.triggerAction", { id: extensionId, targetId: tabId });
  const popupUrl = `chrome-extension://${extensionId}/popup.html`;
  for (let attempt = 0; attempt < 25; attempt += 1) {
    await sleep(200);
    const all = (await browser.send("Target.getTargets", { filter: [{}] })).targetInfos;
    const popup = all.find((target) => target.type === "page" && target.url.startsWith(popupUrl));
    if (popup) {
      const evaluate = await attach(browser, popup.targetId);
      await waitFor(evaluate, `document.body.dataset.state !== 'checking'`, 5000);
      return { targetId: popup.targetId, evaluate };
    }
  }
  throw new Error("the extension's popup did not open");
}

async function closeTarget(browser, targetId) {
  await browser.send("Target.closeTarget", { targetId }).catch(() => {});
}

async function clickExportAndWait(popup, timeoutMs) {
  await popup.evaluate(`document.getElementById('export').click()`);
  await waitFor(popup.evaluate, `['done', 'failed'].includes(document.body.dataset.state)`, timeoutMs);
  return popup.evaluate(POPUP_VIEW);
}

// --- The checks ---------------------------------------------------------------------

let failures = 0;
function check(name, ok, detail = "") {
  if (!ok) failures += 1;
  console.log(`  ${ok ? "ok  " : "FAIL"}  ${name}${detail ? `  (${detail})` : ""}`);
}

async function checkPlaylist(browser, extensionId, url, downloads) {
  for (const old of readdirSync(downloads)) rmSync(join(downloads, old), { force: true });
  const page = await openTab(browser, url);
  const shown = await waitFor(
    page.evaluate,
    `!!document.querySelector('[data-testid="playlist-tracklist"] a[href*="/track/"]')`,
    45000
  );
  if (!shown) {
    console.log(`\n${url}`);
    check("the page shows a track list", false, await page.evaluate("document.title"));
    await closeTarget(browser, page.targetId);
    return;
  }
  await sleep(1500);
  // What the page says about itself, read without the extension's help.
  const truth = await page.evaluate(`({
    title: document.querySelector('[data-testid="entityTitle"]').textContent,
    count: Number(document.querySelector('meta[name="music:song_count"]')?.content),
    firstIds: [...document.querySelectorAll('meta[name="music:song"]')].map((meta) => meta.content.split('/track/')[1]),
  })`);
  // Start part-way down, as if you had been scrolling.
  await page.evaluate(`${SCROLLER}.scrollTop = 900`);
  await sleep(700);
  const startTop = await page.evaluate(`${SCROLLER}.scrollTop`);
  console.log(`\n${truth.title}: the page says ${truth.count} songs`);

  const popup = await openPopup(browser, extensionId, page.tabId);
  const opened = await popup.evaluate(POPUP_VIEW);
  check("the popup offers to export", opened.state === "ready", opened.headline);
  const started = Date.now();
  const view = await clickExportAndWait(popup, 120000);
  const seconds = ((Date.now() - started) / 1000).toFixed(1);
  check("the export finishes", view.state === "done", `${view.headline}${view.state === "failed" ? `: ${view.detail} ${view.lookup}` : `, ${seconds} s`}`);
  check(
    "the popup says what to run next, and it fits",
    view.command === "python -m daily_mix_sync sync-downloads" && view.fits,
    `${view.lookup} ${view.command}`
  );
  await sleep(1200);

  const files = readdirSync(downloads).filter((name) => name.endsWith(".json"));
  check("one file is downloaded", files.length === 1, files.join(", ") || "none");
  if (files.length === 1) {
    const data = JSON.parse(readFileSync(join(downloads, files[0]), "utf8"));
    const ids = data.tracks.map((track) => track.spotify_track_id);
    const known = truth.firstIds.length;
    check("the playlist name is the page's", data.playlist_name === truth.title, data.playlist_name);
    check("the track count is the page's", data.tracks.length === truth.count, `${data.tracks.length} exported`);
    check(
      `the first ${known} tracks are the ones the page lists, in order`,
      known > 0 && JSON.stringify(ids.slice(0, known)) === JSON.stringify(truth.firstIds)
    );
    check("no track appears twice", new Set(ids).size === ids.length, `${new Set(ids).size} different IDs`);
    check(
      "every track has a title, an artist, an ID and a plain track address",
      data.tracks.every(
        (track) =>
          track.title &&
          track.artist &&
          /^[A-Za-z0-9]{22}$/.test(track.spotify_track_id) &&
          track.spotify_url === `https://open.spotify.com/track/${track.spotify_track_id}`
      )
    );
    check(
      "every track has an album and a duration in whole milliseconds",
      data.tracks.every((track) => track.album && Number.isInteger(track.duration_ms) && track.duration_ms > 0)
    );
    check("there are no warnings", view.warnings.length === 0, view.warnings.join(" | "));
    const several = data.tracks.filter((track) => track.artist.includes(", ")).length;
    console.log(`        first: ${data.tracks[0].title} — ${data.tracks[0].artist}`);
    console.log(`        last:  ${data.tracks.at(-1).title} — ${data.tracks.at(-1).artist}`);
    console.log(`        ${several} tracks with several artists; saved as ${files[0]}`);
    if (keepAt) cpSync(join(downloads, files[0]), join(keepAt, files[0]));
  }
  const endTop = await page.evaluate(`${SCROLLER}.scrollTop`);
  check("the page is scrolled back to where it was", endTop === startTop, `${startTop} before, ${endTop} after`);
  await closeTarget(browser, popup.targetId);
  await closeTarget(browser, page.targetId);
}

async function checkOtherPages(browser, extensionId) {
  console.log("\nPages that are not playlists");
  for (const [url, expected] of [
    ["about:blank", "elsewhere"],
    ["https://open.spotify.com/", "spotify"],
  ]) {
    const page = await openTab(browser, url);
    await sleep(url === "about:blank" ? 500 : 4000);
    const popup = await openPopup(browser, extensionId, page.tabId);
    const view = await popup.evaluate(POPUP_VIEW);
    const offered = await popup.evaluate(`!document.getElementById('export').hidden`);
    check(`${url}: the popup declines`, view.state === expected && !offered, view.headline);
    await closeTarget(browser, popup.targetId);
    await closeTarget(browser, page.targetId);
  }

  const page = await openTab(browser, MISSING_PLAYLIST);
  await sleep(5000);
  const popup = await openPopup(browser, extensionId, page.tabId);
  const view = await clickExportAndWait(popup, 30000);
  check(
    "a playlist that does not exist: the export fails and names what it looked for",
    view.state === "failed" && /track list was not found/.test(view.detail) && view.lookup.includes("playlist-tracklist"),
    `${view.headline}: ${view.detail}`
  );
  await closeTarget(browser, popup.targetId);
  await closeTarget(browser, page.targetId);
}

// --- Run ----------------------------------------------------------------------------

if (!existsSync(CHROME)) {
  console.error(`Google Chrome was not found at ${CHROME}. Set CHROME_PATH to its program file.`);
  process.exit(2);
}
if (keepAt) mkdirSync(keepAt, { recursive: true });
const work = mkdtempSync(join(tmpdir(), "daily-mix-sync-live-"));
const downloads = join(work, "downloads");
mkdirSync(downloads);
const browser = startChrome(join(work, "profile"));
try {
  const version = await browser.send("Browser.getVersion");
  const { id } = await browser.send("Extensions.loadUnpacked", { path: EXTENSION });
  await browser.send("Browser.setDownloadBehavior", { behavior: "allow", downloadPath: downloads });
  console.log(`${version.product}, hidden, throwaway profile, signed out. Extension loaded from ${EXTENSION}`);
  // Chrome starts with one blank tab. A second tab keeps the browser open while tabs
  // come and go; it shows the extension's manifest, which no check opens.
  await browser.send("Target.createTarget", { url: `chrome-extension://${id}/manifest.json`, background: true });
  for (const target of (await browser.send("Target.getTargets")).targetInfos) {
    if (target.type === "page" && target.url === "about:blank") await closeTarget(browser, target.targetId);
  }
  for (const url of playlists) await checkPlaylist(browser, id, url, downloads);
  if (!urls.length) await checkOtherPages(browser, id);
} catch (error) {
  failures += 1;
  console.error(`\nThe check could not run: ${error.message}`);
} finally {
  await browser.close();
  rmSync(work, { recursive: true, force: true });
}
console.log(failures ? `\n${failures} check(s) FAILED.` : "\nAll checks passed.");
if (keepAt) console.log(`Exports kept in ${keepAt}`);
process.exit(failures ? 1 : 0);
