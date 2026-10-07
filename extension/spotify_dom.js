// Everything this extension knows about Spotify's page markup lives in this file.
// When Spotify changes its web player, this is the file to fix.
//
// Checked against public playlists on open.spotify.com, signed out, on 2026-10-07. A
// playlist page looked like this (class names left out; they are generated and change
// between releases):
//
//   <span data-testid="entityTitle"><h1>Playlist Name</h1></span>
//   ...
//   <div role="grid" data-testid="playlist-tracklist"
//        aria-rowcount="51" aria-label="Playlist Name">      51 = 50 songs + header row
//     <div role="row" aria-rowindex="1"> <div role="columnheader">…</div> … </div>
//     <div role="row" aria-rowindex="2">                     the first song
//       <div data-testid="tracklist-row">
//         <div role="gridcell" aria-colindex="1">  1  </div>
//         <div role="gridcell" aria-colindex="2">
//           <a data-testid="internal-track-link" href="/track/ID"><div>Title</div></a>
//           <span aria-label="Explicit">E</span>             only on explicit songs
//           <a href="/artist/ID">Artist One</a>, <a href="/artist/ID">Artist Two</a>
//         </div>
//         <div role="gridcell" aria-colindex="3"> <a href="/album/ID">Album</a> </div>
//         <div role="gridcell" aria-colindex="4"> 2 weeks ago </div>       not always there
//         <div role="gridcell" aria-colindex="5"> … <div>3:45</div> … </div>
//       </div>
//     </div>
//     …
//   </div>
//
// Only the rows near the viewport exist in the page at any moment (about 25 to 55 of
// them). The scrolling element is an ancestor of the grid with overflow-y: scroll.
//
// Nothing here changes the page. These functions only read it.
(function (root) {
  "use strict";

  const ns = (root.DailyMixSync = root.DailyMixSync || {});
  if (ns.dom) return; // already loaded into this page
  const format = ns.format;
  if (!format) throw new Error("export_format.js must be loaded before spotify_dom.js");

  const SELECTORS = {
    tracklist: '[data-testid="playlist-tracklist"]',
    playlistTitle: '[data-testid="entityTitle"]',
    row: '[role="row"][aria-rowindex]',
    headerCell: '[role="columnheader"]',
    cell: '[role="gridcell"]',
    trackLink: 'a[href*="/track/"]',
    artistLink: 'a[href*="/artist/"]',
    albumLink: 'a[href*="/album/"]',
    episodeLink: 'a[href*="/episode/"]',
  };

  const ATTRIBUTES = {
    rowCount: "aria-rowcount", // on the grid: rows in the whole playlist, header included
    rowIndex: "aria-rowindex", // on each row: its place in the grid, starting at 1
    listName: "aria-label", // on the grid: the playlist name
  };

  const SCROLLING = new Set(["auto", "scroll", "overlay"]);

  function text(element) {
    return element ? element.textContent.trim() : "";
  }

  function rowIndex(rowElement) {
    return Number.parseInt(rowElement.getAttribute(ATTRIBUTES.rowIndex), 10);
  }

  function isHeaderRow(rowElement) {
    return rowElement.querySelector(SELECTORS.headerCell) !== null;
  }

  function findTracklist(doc) {
    return doc.querySelector(SELECTORS.tracklist);
  }

  // The name as the page shows it, or "" when it cannot be found.
  function readPlaylistName(doc, tracklist) {
    const title = text(doc.querySelector(SELECTORS.playlistTitle));
    if (title) return title;
    return ((tracklist && tracklist.getAttribute(ATTRIBUTES.listName)) || "").trim();
  }

  // How many songs the playlist says it has. `expected` is null when the page does
  // not say; rows are numbered from headerRows + 1.
  function tracklistInfo(tracklist) {
    const rowCount = Number.parseInt(tracklist.getAttribute(ATTRIBUTES.rowCount), 10);
    let headerRows = 0;
    for (const row of tracklist.querySelectorAll(SELECTORS.row)) {
      if (isHeaderRow(row)) headerRows = Math.max(headerRows, rowIndex(row) || 0);
    }
    const expected =
      Number.isInteger(rowCount) && rowCount >= headerRows ? rowCount - headerRows : null;
    return { rowCount, headerRows, expected };
  }

  // The song rows that exist in the page right now, top to bottom.
  function renderedRows(tracklist) {
    return [...tracklist.querySelectorAll(SELECTORS.row)].filter((row) => !isHeaderRow(row));
  }

  function renderedPositions(tracklist, headerRows) {
    return renderedRows(tracklist).map((row) => rowIndex(row) - headerRows);
  }

  // The duration is the only thing in a row written like a clock. It is looked for
  // from the last cell backwards, and never in the cell holding the title, so a song
  // called "4:33" is not mistaken for one.
  function readDuration(rowElement, titleLink) {
    const titleCell = titleLink.closest(SELECTORS.cell);
    const cells = [...rowElement.querySelectorAll(SELECTORS.cell)].reverse();
    for (const cell of cells) {
      if (cell === titleCell) continue;
      for (const element of cell.querySelectorAll("*")) {
        if (element.childElementCount > 0) continue;
        const duration = format.parseDuration(element.textContent);
        if (duration !== null) return duration;
      }
    }
    return null;
  }

  // Read one row. `problem` is null for a usable song; otherwise it names what was
  // missing and which selector looked for it, and the row is left out of the export.
  function readRow(rowElement, headerRows) {
    const row = {
      position: rowIndex(rowElement) - headerRows,
      title: "",
      artists: [],
      album: "",
      durationMs: null,
      trackId: null,
      problem: null,
    };
    const link = rowElement.querySelector(SELECTORS.trackLink);
    if (!link) {
      const episode = rowElement.querySelector(SELECTORS.episodeLink) !== null;
      row.problem = {
        selector: "trackLink",
        reason: episode ? "a podcast episode, not a song" : "no link to a Spotify track (a local file?)",
        text: rowElement.textContent.replace(/\s+/g, " ").trim().slice(0, 120),
      };
      return row;
    }
    row.trackId = format.trackIdFromHref(link.getAttribute("href"));
    row.title = text(link);
    row.artists = [...rowElement.querySelectorAll(SELECTORS.artistLink)].map(text).filter(Boolean);
    row.album = text(rowElement.querySelector(SELECTORS.albumLink));
    row.durationMs = readDuration(rowElement, link);
    if (!row.title) {
      row.problem = { selector: "trackLink", reason: "the track link has no title text", text: "" };
    } else if (row.artists.length === 0) {
      row.problem = { selector: "artistLink", reason: "no artist links", text: row.title };
    }
    return row;
  }

  // The element that scrolls the track list: the nearest ancestor set to scroll
  // vertically. Found by its behaviour, not by a class name.
  function findScroller(tracklist) {
    const view = tracklist.ownerDocument.defaultView;
    for (let element = tracklist.parentElement; element; element = element.parentElement) {
      if (SCROLLING.has(view.getComputedStyle(element).overflowY)) return element;
    }
    return null;
  }

  // How many elements each selector matches right now, for debug output. The first
  // two are looked for on the whole page, the rest inside the track list only.
  function selectorCounts(doc) {
    const tracklist = findTracklist(doc);
    const counts = {};
    for (const [name, selector] of Object.entries(SELECTORS)) {
      const scope = name === "tracklist" || name === "playlistTitle" ? doc : tracklist;
      counts[name] = scope ? scope.querySelectorAll(selector).length : 0;
    }
    return counts;
  }

  // An element's structure as indented text, for debug output: tags, attributes
  // (without class, style and image addresses) and text.
  function describeElement(element, limit = 2500) {
    const lines = [];
    const walk = (node, depth) => {
      const pad = "  ".repeat(depth);
      if (node.nodeType === 3) {
        if (node.textContent.trim()) lines.push(pad + JSON.stringify(node.textContent));
        return;
      }
      if (node.nodeType !== 1) return;
      const tag = node.tagName.toLowerCase();
      if (tag === "svg") {
        lines.push(`${pad}<svg>`);
        return;
      }
      const attributes = [...node.attributes]
        .filter((attribute) => !["class", "style", "src", "srcset"].includes(attribute.name))
        .map((attribute) => `${attribute.name}=${JSON.stringify(attribute.value.slice(0, 80))}`);
      lines.push(`${pad}<${[tag, ...attributes].join(" ")}>`);
      for (const child of node.childNodes) walk(child, depth + 1);
    };
    walk(element, 0);
    const out = lines.join("\n");
    return out.length > limit ? `${out.slice(0, limit)}\n… (cut)` : out;
  }

  ns.dom = {
    SELECTORS,
    ATTRIBUTES,
    findTracklist,
    readPlaylistName,
    tracklistInfo,
    renderedRows,
    renderedPositions,
    readRow,
    findScroller,
    selectorCounts,
    describeElement,
  };
  if (typeof module === "object" && module.exports) module.exports = ns.dom;
})(globalThis);
