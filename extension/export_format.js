// Pure helpers: no DOM and no chrome.* APIs, so they run unchanged in the page, in
// the popup and under Node for the tests.
//
// Nothing here tidies up titles or artist names. Text is passed on exactly as Spotify
// shows it; all normalization happens in the Python application.
(function (root) {
  "use strict";

  const ns = (root.DailyMixSync = root.DailyMixSync || {});
  if (ns.format) return; // already loaded into this page

  const SPOTIFY_ORIGIN = "https://open.spotify.com";
  const SPOTIFY_HOST = "open.spotify.com";
  // Signed-out pages can carry a language prefix: /intl-de/track/...
  const TRACK_PATH = /^\/(?:intl-[a-z-]+\/)?track\/([A-Za-z0-9]+)\/?$/;
  const PLAYLIST_PATH = /^\/(?:intl-[a-z-]+\/)?playlist\/([A-Za-z0-9]+)\/?$/;
  const TRACK_URI = /^spotify:track:([A-Za-z0-9]+)$/;
  // m:ss, mm:ss or h:mm:ss. With hours, minutes are always two digits.
  const CLOCK = /^(?:(\d{1,3}):(\d{2})|(\d{1,2})):(\d{2})$/;
  // Invisible direction marks that can surround numbers in right-to-left layouts.
  const DIRECTION_MARKS = /[‎‏‪-‮⁦-⁩]/g;

  function spotifyUrl(href) {
    if (typeof href !== "string" || !href.trim()) return null;
    let url;
    try {
      url = new URL(href.trim(), SPOTIFY_ORIGIN + "/");
    } catch {
      return null;
    }
    return url.protocol === "https:" && url.hostname === SPOTIFY_HOST ? url : null;
  }

  // "3:42" -> 222000, "1:03:15" -> 3795000. Anything else -> null; never a guess.
  function parseDuration(text) {
    if (typeof text !== "string") return null;
    const match = CLOCK.exec(text.replace(DIRECTION_MARKS, "").trim());
    if (!match) return null;
    const hasHours = match[1] !== undefined;
    const hours = hasHours ? Number(match[1]) : 0;
    const minutes = Number(hasHours ? match[2] : match[3]);
    const seconds = Number(match[4]);
    if (seconds > 59 || (hasHours && minutes > 59)) return null;
    const ms = ((hours * 60 + minutes) * 60 + seconds) * 1000;
    return ms > 0 ? ms : null;
  }

  // "/track/ABC123?si=..." or a full open.spotify.com link -> "ABC123". Else null.
  function trackIdFromHref(href) {
    if (typeof href !== "string") return null;
    const uri = TRACK_URI.exec(href.trim());
    if (uri) return uri[1];
    const url = spotifyUrl(href);
    const match = url && TRACK_PATH.exec(url.pathname);
    return match ? match[1] : null;
  }

  function canonicalTrackUrl(trackId) {
    return `${SPOTIFY_ORIGIN}/track/${trackId}`;
  }

  function playlistIdFromUrl(href) {
    const url = spotifyUrl(href);
    const match = url && PLAYLIST_PATH.exec(url.pathname);
    return match ? match[1] : null;
  }

  // What kind of page a tab is showing: "playlist", "spotify" (some other Spotify
  // page) or "elsewhere".
  function pageKind(href) {
    if (!spotifyUrl(href)) return "elsewhere";
    return playlistIdFromUrl(href) ? "playlist" : "spotify";
  }

  // "Daily Mix 1" -> "daily_mix_1.json". Letters and digits of any script are kept.
  function exportFilename(playlistName) {
    const words = String(playlistName ?? "")
      .normalize("NFKC")
      .toLowerCase()
      .replace(/['’`]/g, "")
      .replace(/[^\p{L}\p{N}\p{M}]+/gu, "_")
      .replace(/^_+|_+$/g, "");
    const base = Array.from(words).slice(0, 80).join("").replace(/_+$/, "");
    return `${base || "spotify_playlist"}.json`;
  }

  function sameTrack(a, b) {
    if (a.trackId || b.trackId) return a.trackId === b.trackId;
    return a.title === b.title && a.artists.join("\u001f") === b.artists.join("\u001f");
  }

  // Add rows read from the page to `collected`, a Map keyed by playlist position.
  //
  // Spotify draws only the rows near the viewport, so the same row is read many times
  // while scrolling; keying by position stores each one once and keeps a song that
  // really is in the playlist twice. A position that turns up holding a different
  // track means the list changed under us, and is reported as a conflict.
  function mergeRows(collected, rows) {
    let added = 0;
    const conflicts = [];
    for (const row of rows) {
      const seen = collected.get(row.position);
      if (seen === undefined) {
        collected.set(row.position, row);
        added += 1;
      } else if (seen.problem && !row.problem) {
        collected.set(row.position, row); // it had not finished drawing the first time
      } else if (!seen.problem && !row.problem && !sameTrack(seen, row)) {
        conflicts.push(row.position);
      }
    }
    return { added, conflicts };
  }

  // The lowest position in 1..expected not collected yet, or null when all are there.
  function firstMissing(collected, expected) {
    for (let position = 1; position <= expected; position += 1) {
      if (!collected.has(position)) return position;
    }
    return null;
  }

  function missingPositions(collected, expected) {
    const missing = [];
    for (let position = 1; position <= expected; position += 1) {
      if (!collected.has(position)) missing.push(position);
    }
    return missing;
  }

  // [3, 4, 5, 9] -> "3–5, 9"
  function formatRanges(numbers) {
    const sorted = [...numbers].sort((a, b) => a - b);
    const parts = [];
    for (let i = 0; i < sorted.length; i += 1) {
      let end = i;
      while (end + 1 < sorted.length && sorted[end + 1] === sorted[end] + 1) end += 1;
      parts.push(end > i ? `${sorted[i]}–${sorted[end]}` : `${sorted[i]}`);
      i = end;
    }
    return parts.join(", ");
  }

  // One track in the shape the Python importer reads. A value Spotify did not show
  // is left out rather than invented.
  function trackEntry(row) {
    const entry = { title: row.title, artist: row.artists.join(", ") };
    if (row.album) entry.album = row.album;
    if (Number.isInteger(row.durationMs) && row.durationMs > 0) entry.duration_ms = row.durationMs;
    if (row.trackId) {
      entry.spotify_track_id = row.trackId;
      entry.spotify_url = canonicalTrackUrl(row.trackId);
    }
    return entry;
  }

  // The whole export. `rows` may be in any order; they are written in playlist order.
  function buildExport({ playlistName, sourceUrl, exportedAt, rows }) {
    const ordered = [...rows].sort((a, b) => a.position - b.position);
    const data = { playlist_name: playlistName };
    if (sourceUrl) data.source_url = sourceUrl;
    if (exportedAt) data.exported_at = exportedAt;
    data.tracks = ordered.map(trackEntry);
    return data;
  }

  function serializeExport(data) {
    return `${JSON.stringify(data, null, 2)}\n`;
  }

  ns.format = {
    parseDuration,
    trackIdFromHref,
    canonicalTrackUrl,
    playlistIdFromUrl,
    pageKind,
    exportFilename,
    mergeRows,
    firstMissing,
    missingPositions,
    formatRanges,
    trackEntry,
    buildExport,
    serializeExport,
  };
  if (typeof module === "object" && module.exports) module.exports = ns.format;
})(globalThis);
