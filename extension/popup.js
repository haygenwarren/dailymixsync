// The popup: decides whether the current tab is a Spotify playlist, starts the export
// in that tab, shows progress, and saves the finished file.
//
// States (body[data-state]): checking, elsewhere, spotify, ready, scanning, done, failed.
(function () {
  "use strict";

  const { format } = globalThis.DailyMixSync;
  const PORT_NAME = "daily-mix-sync"; // must match content.js
  const PAGE_SCRIPTS = ["export_format.js", "spotify_dom.js", "content.js"]; // in this order
  // What to do with the saved file. Shown as text to copy; nothing here can run it.
  const NEXT_COMMAND = "python -m daily_mix_sync sync-downloads";

  const $ = (id) => document.getElementById(id);
  let tab = null;
  let port = null;
  let state = "checking";
  let result = null; // the finished export
  let debugText = ""; // what "Copy debug info" copies

  function show(id, visible) {
    $(id).hidden = !visible;
  }

  function setText(id, value) {
    $(id).textContent = value || "";
    show(id, Boolean(value));
  }

  function render(next, view = {}) {
    state = next;
    document.body.dataset.state = next;
    setText("headline", view.headline);
    setText("detail", view.detail);
    setText("lookup", view.lookup);
    setText("command", view.command);
    show("progress", next === "scanning");
    $("warnings").replaceChildren(
      ...(view.warnings || []).map((warning) => {
        const item = document.createElement("li");
        item.textContent = warning;
        return item;
      })
    );
    show("warnings", (view.warnings || []).length > 0);
    $("export").textContent = next === "ready" ? "Export this playlist" : "Export again";
    show("export", ["ready", "done", "failed"].includes(next) && view.canExport !== false);
    show("download", next === "done");
    show("copy-debug", Boolean(debugText) && ["done", "failed"].includes(next));
    $("copy-debug").textContent = "Copy debug info";
    show("debug-option", ["ready", "done", "failed"].includes(next) && view.canExport !== false);
  }

  function showProgress(collected, expected) {
    const bar = $("progress");
    if (expected) {
      bar.max = expected;
      bar.value = collected;
      setText("headline", `Reading tracks… ${collected} of ${expected}`);
    } else {
      bar.removeAttribute("value"); // indeterminate until the page says how many
      setText("headline", "Reading tracks…");
    }
  }

  function fail(error, canExport = true) {
    debugText = error.debug || "";
    const lookup = error.selectorText
      ? `Looked for “${error.selector}” in spotify_dom.js: ${error.selectorText}`
      : "";
    render("failed", {
      headline: "Export failed",
      detail: error.message,
      lookup,
      canExport,
    });
  }

  function download() {
    const url = URL.createObjectURL(new Blob([result.json], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = result.filename;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  }

  function finish(done) {
    result = done;
    debugText = done.debug || "";
    download();
    const count = `${done.trackCount} ${done.trackCount === 1 ? "track" : "tracks"}`;
    render("done", {
      headline: `Saved ${done.filename}`,
      detail: `${count} from “${done.playlistName}”. First: ${done.first}. Last: ${done.last}.`,
      warnings: done.warnings,
      lookup: "Export your other Daily Mixes, then run:",
      command: NEXT_COMMAND,
    });
  }

  function closePort() {
    if (!port) return;
    const closing = port;
    port = null;
    try {
      closing.disconnect();
    } catch {
      // already closed
    }
  }

  function onMessage(message) {
    if (message.type === "progress") {
      showProgress(message.collected, message.expected);
    } else if (message.type === "done") {
      closePort();
      finish(message.result);
    } else if (message.type === "failed") {
      closePort();
      fail(message.error);
    }
  }

  async function startExport() {
    closePort();
    result = null;
    debugText = "";
    render("scanning", { detail: "Keep this popup open. The playlist scrolls by itself, then goes back to where it was." });
    showProgress(0, null);
    try {
      await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: PAGE_SCRIPTS });
    } catch (error) {
      fail({ message: `The Spotify tab could not be read: ${error.message}` });
      return;
    }
    const opened = chrome.tabs.connect(tab.id, { name: PORT_NAME });
    port = opened;
    opened.onMessage.addListener(onMessage);
    opened.onDisconnect.addListener(() => {
      void chrome.runtime.lastError; // read it so Chrome does not log it as unhandled
      if (port !== opened) return;
      port = null;
      if (state === "scanning") {
        fail({ message: "The Spotify tab stopped answering. Was it reloaded or closed? Try again." });
      }
    });
    opened.postMessage({ type: "scan", debug: $("debug").checked });
  }

  async function copyDebug() {
    try {
      await navigator.clipboard.writeText(debugText);
      $("copy-debug").textContent = "Copied";
    } catch {
      $("copy-debug").textContent = "Could not copy";
    }
  }

  async function init() {
    $("export").addEventListener("click", startExport);
    $("download").addEventListener("click", download);
    $("copy-debug").addEventListener("click", copyDebug);
    try {
      [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    } catch {
      tab = null;
    }
    const kind = format.pageKind(tab && tab.url);
    if (kind === "playlist") {
      render("ready", { headline: "Ready to export the playlist in this tab." });
    } else if (kind === "spotify") {
      render("spotify", {
        headline: "This Spotify page is not a playlist.",
        detail: "Open a Daily Mix, then click this button again.",
        canExport: false,
      });
    } else {
      render("elsewhere", {
        headline: "This tab is not Spotify.",
        detail: "Open a Daily Mix on open.spotify.com, then click this button again.",
        canExport: false,
      });
    }
  }

  init();
})();
