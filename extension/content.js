// Runs inside the Spotify tab when the popup asks for an export: scrolls the playlist
// from top to bottom, reads every row on the way, and hands the result to the popup.
//
// READ-ONLY. The one thing this file changes on the page is the scroll position of
// the playlist, which it puts back when it is done. It never clicks, types, focuses
// or sends anything, and makes no network requests. Reading the markup is left to
// spotify_dom.js.
(function (root) {
  "use strict";

  const ns = (root.DailyMixSync = root.DailyMixSync || {});
  if (ns.scan) return; // already loaded into this page
  const { format, dom } = ns;
  if (!format || !dom) {
    throw new Error("export_format.js and spotify_dom.js must be loaded before content.js");
  }

  const PORT_NAME = "daily-mix-sync";

  const TIMING = {
    pollMs: 50, // how often the page is looked at again while waiting
    tracklistWaitMs: 8000, // a page still loading gets this long to show its track list
    settleMs: 1000, // longest wait for a redraw after a scroll step, to begin with
    stallMs: 10000, // give up when no new row has appeared for this long
    stepFraction: 0.8, // one scroll step, as a share of the visible height
    minStepPx: 200,
  };

  // A failure the popup can explain. `selector` names the entry in spotify_dom.js
  // that found nothing, when that is the cause.
  class ScanError extends Error {
    constructor(step, message, selector = null) {
      super(message);
      this.name = "ScanError";
      this.step = step;
      this.selector = selector;
    }
  }

  function describeTrack(row) {
    return `${row.title} — ${row.artists.join(", ")}`;
  }

  // Read the playlist in `env.document`.
  //
  // env: document, sleep(ms), now() in ms, timestamp() for the export; optionally
  // debug (also log to the console), isCancelled(), onProgress({collected, expected}),
  // version, timing (overrides, for tests).
  //
  // Resolves to the finished export. Rejects with a ScanError, or with whatever went
  // wrong unexpectedly; either way the error carries `debug`, the text behind
  // "Copy debug info".
  async function scanPlaylist(env) {
    const doc = env.document;
    const timing = { ...TIMING, ...(env.timing || {}) };
    const cancelled = env.isCancelled || (() => false);
    const progress = env.onProgress || (() => {});
    const started = env.now();
    const lines = [];
    const note = (message) => {
      lines.push(`${String(Math.round(env.now() - started)).padStart(6)} ms  ${message}`);
      if (env.debug) console.log("[Daily Mix Sync]", message);
    };
    let sample = null; // markup of one row, kept for the debug text

    const stopIfCancelled = () => {
      if (cancelled()) throw new ScanError("cancelled", "The export was stopped.");
    };
    const waitFor = async (check, timeoutMs) => {
      const deadline = env.now() + timeoutMs;
      for (;;) {
        const value = check();
        if (value) return value;
        if (env.now() >= deadline) return null;
        stopIfCancelled();
        await env.sleep(timing.pollMs);
      }
    };
    const debugText = (outcome) => {
      const view = doc.defaultView;
      const out = [
        `Daily Mix Sync extension ${env.version || ""}`.trim(),
        `Outcome: ${outcome}`,
        `Page: ${doc.location.href}`,
        `Browser: ${view.navigator.userAgent}`,
        `Window: ${view.innerWidth}x${view.innerHeight}`,
        `Selector matches now: ${Object.entries(dom.selectorCounts(doc)).map(([name, count]) => `${name}=${count}`).join("  ")}`,
        "",
        "Steps:",
        ...lines.slice(-300),
      ];
      if (sample) out.push("", `Markup of ${sample.what}:`, sample.markup);
      return out.join("\n");
    };

    try {
      if (format.pageKind(doc.location.href) !== "playlist") {
        throw new ScanError("page", "This page is not a Spotify playlist.");
      }
      const tracklist = await waitFor(() => dom.findTracklist(doc), timing.tracklistWaitMs);
      if (!tracklist) {
        throw new ScanError("tracklist", "The track list was not found on this page.", "tracklist");
      }
      const playlistName = dom.readPlaylistName(doc, tracklist);
      if (!playlistName) {
        throw new ScanError("name", "The playlist name could not be read from the page.", "playlistTitle");
      }
      const scroller = dom.findScroller(tracklist);
      const startTop = scroller ? scroller.scrollTop : 0;
      note(`playlist ${JSON.stringify(playlistName)}; ${dom.ATTRIBUTES.rowCount}=${tracklist.getAttribute(dom.ATTRIBUTES.rowCount)}`);
      note(scroller ? `scrolling element found, at ${startTop} of ${scroller.scrollHeight}` : "no scrolling element found");

      const collected = new Map();
      let expected = null;
      try {
        let lastProgress = env.now();
        let patience = timing.settleMs; // longest wait for a redraw; grows on a slow page
        let seekingFirst = false; // the first row was not on the page; looking for it
        for (;;) {
          stopIfCancelled();
          if (!tracklist.isConnected) {
            throw new ScanError("changed", "The page changed while the playlist was being read. Try again.");
          }
          const info = dom.tracklistInfo(tracklist);
          if (info.expected === null) {
            throw new ScanError(
              "count",
              `The track list does not say how many rows it has (${dom.ATTRIBUTES.rowCount}).`,
              "tracklist"
            );
          }
          expected = info.expected;

          const elements = dom.renderedRows(tracklist);
          const rows = elements.map((element) => dom.readRow(element, info.headerRows));
          if (rows.some((row) => !Number.isInteger(row.position) || row.position < 1)) {
            throw new ScanError(
              "rows",
              `The rows no longer say where they are in the playlist (${dom.ATTRIBUTES.rowIndex}).`,
              "row"
            );
          }
          const unreadable = rows.findIndex((row) => row.problem);
          if (unreadable >= 0 && !(sample && sample.unreadable)) {
            sample = {
              what: `row ${rows[unreadable].position}, which could not be read`,
              markup: dom.describeElement(elements[unreadable]),
              unreadable: true,
            };
          } else if (!sample && rows.length) {
            sample = {
              what: `row ${rows[0].position}`,
              markup: dom.describeElement(elements[0]),
              unreadable: false,
            };
          }

          const merged = format.mergeRows(collected, rows);
          if (merged.conflicts.length) {
            throw new ScanError(
              "changed",
              `The playlist changed while it was being read (row ${merged.conflicts[0]}). Try again.`
            );
          }
          const positions = rows.map((row) => row.position);
          const lowest = Math.min(...positions);
          const highest = Math.max(...positions);
          if (merged.added) {
            lastProgress = env.now();
            note(`rows ${lowest}–${highest} in the page; ${collected.size} of ${expected} read`);
            progress({ collected: collected.size, expected });
          }

          const need = format.firstMissing(collected, expected);
          if (need === null) break;
          if (env.now() - lastProgress > timing.stallMs) {
            const where = scroller
              ? `scrolled to ${Math.round(scroller.scrollTop)} of ${scroller.scrollHeight}`
              : "nothing to scroll";
            note(`stalled waiting for row ${need}; ${where}; rows in the page: ${rows.length ? `${lowest}–${highest}` : "none"}`);
            throw new ScanError(
              "stalled",
              `Row ${need} of ${expected} never appeared, so the export would be incomplete. ` +
                `${collected.size} rows were read.`,
              "row"
            );
          }
          // Move towards the row that is needed next: straight to the top for the
          // first row, otherwise one step. A step is shorter than the visible height,
          // so the rows drawn after it overlap the ones before.
          if (scroller) {
            const step = Math.max(timing.minStepPx, Math.round(scroller.clientHeight * timing.stepFraction));
            if (rows.length === 0) {
              // No rows on the page. Part-way down that means they are still loading,
              // and waiting is right. Before the first row has been seen, the list may
              // simply be out of view: go to the top, then look downwards from there.
              if (need === 1) {
                scroller.scrollTop = seekingFirst ? scroller.scrollTop + step : 0;
                seekingFirst = true;
              }
            } else if (need > highest) {
              scroller.scrollTop += step;
            } else if (need < lowest) {
              scroller.scrollTop = need === 1 ? 0 : scroller.scrollTop - step;
            }
          }
          // Spotify redraws a moment after a scroll. Wait until it has: either the
          // needed row is there, or the page shows different rows than it did. A page
          // that did neither in time is slow, and gets longer before the next move.
          //
          // Every pass pauses at least once, whatever the page shows. The loop can
          // then never spin and hold up the tab, and the stall check above always
          // gets its turn.
          const before = positions.join(",");
          stopIfCancelled();
          await env.sleep(timing.pollMs);
          const redrawn = await waitFor(() => {
            const drawn = dom.renderedPositions(tracklist, info.headerRows);
            return drawn.includes(need) || (drawn.length > 0 && drawn.join(",") !== before);
          }, patience);
          if (!redrawn) patience = Math.min(patience * 2, timing.stallMs);
        }
      } finally {
        if (scroller) {
          scroller.scrollTop = startTop;
          note(`scroll position put back to ${startTop}`);
        }
      }

      if (expected === 0) throw new ScanError("empty", "This playlist has no songs.");
      const ordered = [...collected.values()].sort((a, b) => a.position - b.position);
      // Every position up to the stated count is there. A row beyond it means the
      // count cannot be trusted, and neither can "complete".
      const last = ordered[ordered.length - 1].position;
      if (last > expected) {
        throw new ScanError(
          "count",
          `The track list says it has ${expected} songs, but row ${last} is on the page.`,
          "tracklist"
        );
      }
      const tracks = ordered.filter((row) => !row.problem);
      const skipped = ordered.filter((row) => row.problem);
      if (tracks.length === 0) {
        const first = skipped[0].problem;
        throw new ScanError(
          "rows",
          `None of the ${ordered.length} rows could be read: ${first.reason}.`,
          first.selector
        );
      }

      const warnings = [];
      if (skipped.length) {
        const shown = skipped
          .slice(0, 5)
          .map((row) => `row ${row.position} (${row.problem.reason})`)
          .join("; ");
        const more = skipped.length > 5 ? `; and ${skipped.length - 5} more` : "";
        warnings.push(`${skipped.length} of ${ordered.length} rows left out: ${shown}${more}.`);
      }
      const count = (missing) => tracks.filter(missing).length;
      const describe = (number, what) =>
        number === tracks.length ? `No ${what} found for any track.` : `${number} of ${tracks.length} tracks have no ${what}.`;
      const noAlbum = count((row) => !row.album);
      const noDuration = count((row) => row.durationMs === null);
      const noId = count((row) => !row.trackId);
      if (noAlbum) warnings.push(describe(noAlbum, "album name"));
      if (noDuration) warnings.push(describe(noDuration, "duration"));
      if (noId) warnings.push(describe(noId, "Spotify track ID"));
      for (const warning of warnings) note(`warning: ${warning}`);

      const data = format.buildExport({
        playlistName,
        sourceUrl: doc.location.origin + doc.location.pathname,
        exportedAt: env.timestamp(),
        rows: tracks,
      });
      note(`done: ${tracks.length} tracks`);
      return {
        playlistName,
        filename: format.exportFilename(playlistName),
        json: format.serializeExport(data),
        trackCount: tracks.length,
        rowCount: ordered.length,
        first: describeTrack(tracks[0]),
        last: describeTrack(tracks[tracks.length - 1]),
        warnings,
        debug: debugText(`exported ${tracks.length} tracks`),
      };
    } catch (error) {
      note(`failed: ${error.message}`);
      error.debug = debugText(`failed at step "${error.step || "unexpected"}"`);
      throw error;
    }
  }

  // What the popup needs to show a failure.
  function describeError(error) {
    const selector = error.selector || null;
    return {
      step: error.step || "unexpected",
      message: error.message || String(error),
      selector,
      selectorText: selector ? dom.SELECTORS[selector] || null : null,
      debug: error.debug || "",
    };
  }

  ns.scan = { PORT_NAME, TIMING, ScanError, scanPlaylist, describeError };
  if (typeof module === "object" && module.exports) module.exports = ns.scan;

  // The popup connects when "Export" is clicked. Closing the popup closes the
  // connection, which stops the scan and puts the scroll position back.
  const runtime = root.chrome && root.chrome.runtime;
  if (runtime && runtime.onConnect) {
    let queue = Promise.resolve(); // one scan at a time
    runtime.onConnect.addListener((port) => {
      if (port.name !== PORT_NAME) return;
      let open = true;
      port.onDisconnect.addListener(() => {
        open = false;
      });
      const post = (message) => {
        if (!open) return;
        try {
          port.postMessage(message);
        } catch {
          open = false;
        }
      };
      port.onMessage.addListener((message) => {
        if (!message || message.type !== "scan") return;
        queue = queue.then(() =>
          scanPlaylist({
            document: root.document,
            sleep: (ms) => new Promise((resolve) => root.setTimeout(resolve, ms)),
            now: () => root.performance.now(),
            timestamp: () => new Date().toISOString(),
            debug: Boolean(message.debug),
            version: runtime.getManifest().version,
            isCancelled: () => !open,
            onProgress: (state) => post({ type: "progress", ...state }),
          }).then(
            (result) => post({ type: "done", result }),
            (error) => post({ type: "failed", error: describeError(error) })
          )
        );
      });
    });
  }
})(globalThis);
