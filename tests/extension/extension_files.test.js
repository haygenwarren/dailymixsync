"use strict";

// What the extension is allowed to do, pinned down: the permissions it asks for, and
// the promise that the code running inside the Spotify tab only reads and scrolls.

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const EXTENSION = path.join(__dirname, "..", "..", "extension");
const read = (name) => fs.readFileSync(path.join(EXTENSION, name), "utf8");
const manifest = JSON.parse(read("manifest.json"));

// The files that are put into the Spotify page.
const PAGE_SCRIPTS = ["export_format.js", "spotify_dom.js", "content.js"];

// Source with comments and string contents removed, so the checks below look at what
// the code does, not at what it says.
function code(name) {
  return read(name)
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "")
    .replace(/\s\/\/ .*$/gm, "")
    .replace(/(["'`])(?:\\.|(?!\1)[^\\\n])*\1/g, '""');
}

test("the folder holds exactly the extension's files", () => {
  assert.deepEqual(fs.readdirSync(EXTENSION).sort(), [
    "README.md",
    "content.js",
    "export_format.js",
    "manifest.json",
    "popup.html",
    "popup.js",
    "spotify_dom.js",
  ]);
});

test("it is a Manifest V3 extension with a popup and nothing running in the background", () => {
  assert.equal(manifest.manifest_version, 3);
  assert.equal(manifest.action.default_popup, "popup.html");
  for (const key of ["background", "content_scripts", "web_accessible_resources", "externally_connectable", "options_page", "options_ui"]) {
    assert.ok(!(key in manifest), `${key} is not used`);
  }
});

test("it asks for activeTab and scripting, and for no site access of its own", () => {
  assert.deepEqual([...manifest.permissions].sort(), ["activeTab", "scripting"]);
  for (const key of ["host_permissions", "optional_permissions", "optional_host_permissions"]) {
    assert.ok(!(key in manifest), `${key} is not used`);
  }
});

test("the popup injects exactly the page scripts, in dependency order", () => {
  const match = /const PAGE_SCRIPTS = (\[[^\]]*\])/.exec(read("popup.js"));
  assert.deepEqual(JSON.parse(match[1]), PAGE_SCRIPTS);
  for (const name of PAGE_SCRIPTS) assert.ok(fs.existsSync(path.join(EXTENSION, name)), name);
});

test("the popup and the page script use the same connection name", () => {
  const inPopup = /const PORT_NAME = "([^"]+)"/.exec(read("popup.js"))[1];
  require("../../extension/export_format.js");
  require("../../extension/spotify_dom.js");
  assert.equal(inPopup, require("../../extension/content.js").PORT_NAME);
});

test("the popup page loads only the extension's own scripts and nothing from the network", () => {
  const html = read("popup.html");
  const scripts = [...html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)];
  assert.deepEqual(scripts.map((script) => /src="([^"]+)"/.exec(script[1])[1]), ["export_format.js", "popup.js"]);
  assert.ok(scripts.every((script) => script[2].trim() === ""), "no inline script");
  assert.doesNotMatch(html, /https?:\/\//);
  assert.doesNotMatch(html, /\son[a-z]+=/, "no inline event handlers");
});

test("the popup has a place for every state it can show", () => {
  const html = read("popup.html");
  for (const id of ["headline", "detail", "progress", "warnings", "lookup", "export", "download", "copy-debug", "debug"]) {
    assert.match(html, new RegExp(`id="${id}"`), id);
  }
  const popup = read("popup.js");
  for (const state of ["elsewhere", "spotify", "ready", "scanning", "done", "failed"]) {
    assert.match(popup, new RegExp(`render\\("${state}"`), state);
  }
});

test("the page scripts never click, type, focus or submit anything", () => {
  const forbidden = /\.(click|focus|blur|submit|select|dispatchEvent|requestSubmit|scrollIntoView|play|pause)\s*\(/;
  for (const name of PAGE_SCRIPTS) {
    assert.doesNotMatch(code(name), forbidden, name);
    assert.doesNotMatch(code(name), /\b(KeyboardEvent|MouseEvent|PointerEvent|InputEvent|CustomEvent)\b/, name);
  }
});

test("the page scripts make no network requests", () => {
  const forbidden = /\b(fetch|XMLHttpRequest|WebSocket|EventSource|sendBeacon|importScripts)\b|\bimport\s*\(/;
  for (const name of PAGE_SCRIPTS) assert.doesNotMatch(code(name), forbidden, name);
});

test("the page scripts read no cookies or stored data", () => {
  const forbidden = /\b(cookie|localStorage|sessionStorage|indexedDB|caches)\b/;
  for (const name of PAGE_SCRIPTS) assert.doesNotMatch(code(name), forbidden, name);
});

test("the page scripts do not add to, remove from or rewrite the page", () => {
  const forbidden =
    /\.(appendChild|append|prepend|before|after|remove|removeChild|replaceChild|replaceWith|replaceChildren|insertBefore|insertAdjacentHTML|insertAdjacentElement|setAttribute|removeAttribute|toggleAttribute|createElement|write)\s*\(|\.(innerHTML|outerHTML|innerText|textContent|value|href|className|checked|hidden)\s*=[^=]|\.(classList|style|dataset)\b/;
  for (const name of PAGE_SCRIPTS) assert.doesNotMatch(code(name), forbidden, name);
});

test("scrollTop is the only property of the page that is ever assigned", () => {
  const assigned = new Set();
  // `something.property = value` or `+=`/`-=`, on anything but plain local objects.
  const pattern = /\b([A-Za-z_$][\w$]*)\.([A-Za-z_$][\w$]*)\s*(?:[+\-*/]?=)(?!=)/g;
  const ownObjects = new Set(["ns", "root", "module", "row", "entry", "data", "error", "counts", "this"]);
  for (const name of PAGE_SCRIPTS) {
    for (const match of code(name).matchAll(pattern)) {
      if (!ownObjects.has(match[1])) assigned.add(`${match[1]}.${match[2]}`);
    }
  }
  assert.deepEqual([...assigned], ["scroller.scrollTop"]);
});

test("the page scripts use the extension API only to talk to the popup", () => {
  const used = new Set();
  for (const name of PAGE_SCRIPTS) {
    for (const match of code(name).matchAll(/\bchrome\.([A-Za-z.]+)/g)) used.add(match[1]);
    for (const match of code(name).matchAll(/\bruntime\.([A-Za-z]+)/g)) used.add(`runtime.${match[1]}`);
  }
  assert.deepEqual([...used].sort(), ["runtime", "runtime.getManifest", "runtime.onConnect"]);
});

test("the popup uses only the extension APIs its permissions cover", () => {
  const used = new Set([...code("popup.js").matchAll(/\bchrome\.([a-z]+)\.([A-Za-z]+)/g)].map((match) => `${match[1]}.${match[2]}`));
  assert.deepEqual([...used].sort(), ["runtime.lastError", "scripting.executeScript", "tabs.connect", "tabs.query"]);
});

test("nothing in the extension reaches outside it: no remote code, no eval", () => {
  for (const name of [...PAGE_SCRIPTS, "popup.js"]) {
    assert.doesNotMatch(code(name), /\beval\s*\(|\bnew Function\b|\bsetInterval\b/, name);
  }
  assert.doesNotMatch(code("popup.js"), /\b(fetch|XMLHttpRequest|WebSocket|sendBeacon)\b/);
});

test("the popup points to sync-downloads and still cannot run anything", () => {
  const popup = read("popup.js");
  assert.match(popup, /const NEXT_COMMAND = "python -m daily_mix_sync sync-downloads";/);
  assert.doesNotMatch(popup, /data\//, "no advice to move the file into data/");
  // The command is shown as text. Nothing opens a program, a link or a native app.
  assert.doesNotMatch(
    code("popup.js"),
    /\b(sendNativeMessage|connectNative|openOptionsPage)\b|\bopen\s*\(|\blocation\s*=|\blocation\.(assign|replace|href)\b/
  );
  assert.doesNotMatch(read("manifest.json"), /nativeMessaging|downloads|tabs"|storage/);
});

test("the version in the manifest is a plain release number", () => {
  assert.match(manifest.version, /^\d+\.\d+\.\d+$/);
});
