"""Metadata normalization, shared by Spotify source tracks and Apple Music candidates.

Both services describe the same recording differently, mostly in the *qualifiers*
attached to a title: "(feat. X)", "- Remastered 2011", "[Live]", "(Deluxe Edition)".
A title is therefore split into:

  base        the title proper, with qualifiers removed
  flags       recognised version markers (live, remix, ...) found in qualifiers
  qualifiers  whatever qualifier words are left over once featured artists,
              flags and edition filler have been taken out

Nothing meaningful is thrown away: an unrecognised qualifier such as
"(Taylor's Version)" or "(Pts. 1-5)" survives as qualifier words, and the matcher
penalises candidates whose qualifier words differ.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .models import SourceTrack

LIVE = "live"
ACOUSTIC = "acoustic"
REMIX = "remix"
REMASTER = "remaster"
RADIO_EDIT = "radio_edit"
DEMO = "demo"
INSTRUMENTAL = "instrumental"

# Applied to normalized qualifier text only, never to the base title, so
# "Live Forever" or "Demons" raise no flag. Matched text is consumed.
_FLAG_PATTERNS = (
    (REMASTER, re.compile(r"\b(?:\d{4} )?(?:digital(?:ly)? )?re ?master(?:ed)?(?: \d{4})?\b")),
    (RADIO_EDIT, re.compile(r"\bradio (?:edit|version|mix)\b")),
    (LIVE, re.compile(r"\blive\b")),
    (ACOUSTIC, re.compile(r"\bacoustic\b")),
    (REMIX, re.compile(r"\b(?:remix(?:ed)?|rmx)\b")),
    (DEMO, re.compile(r"\bdemo\b")),
    (INSTRUMENTAL, re.compile(r"\binstrumental\b")),
)
FLAGS = frozenset(flag for flag, _ in _FLAG_PATTERNS)

# Qualifier words that label an edition or packaging rather than a different
# recording: "(Deluxe Edition)", "(Album Version)", "- Single", "(Bonus Track)".
_FILLER_WORDS = frozenset(
    {
        "version", "edition", "deluxe", "super", "expanded", "special", "anniversary",
        "bonus", "track", "explicit", "album", "single", "ep", "original", "mix", "edit",
    }
)
_ORDINAL = re.compile(r"\d+(?:st|nd|rd|th)")  # "25th" in "(25th Anniversary Edition)"

_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−"), "-")
_APOSTROPHES = "'’‘ʼ`´"
_BRACKETED = re.compile(r"\(([^()]*)\)|\[([^\[\]]*)\]")
_DASH_SEPARATOR = re.compile(r"\s+-\s+")
# Inside a qualifier: "(feat. X)", "(ft X)", "(Remix feat. X)", "(with X)".
_QUALIFIER_FEAT = re.compile(r"(?:^\s*with|\b(?:feat|ft|featuring)\b\.?)\s+(.+)$", re.IGNORECASE)
# In the base title only the unambiguous spellings count: "Song feat. X".
_BARE_FEAT = re.compile(r"\s+(?:feat\.?|ft\.|featuring)\s+(.+)$", re.IGNORECASE)
_ARTIST_SEPARATOR = re.compile(
    r"\s*[,;&+]\s*|\s+(?:and|x|×|vs\.?|with|feat\.?|ft\.?|featuring)\s+", re.IGNORECASE
)


@dataclass(frozen=True)
class NormalizedTrack:
    title: str  # base title
    qualifiers: frozenset[str]
    flags: frozenset[str]
    artists: tuple[str, ...]  # individual names, primary first, featured artists included
    album: str  # "" when unknown
    duration_ms: int | None


def normalize_text(text: str) -> str:
    """Casefold, strip diacritics and punctuation, collapse whitespace."""
    text = unicodedata.normalize("NFKC", text).casefold().replace("&", " and ")
    out = []
    for ch in unicodedata.normalize("NFKD", text):
        category = unicodedata.category(ch)
        if category == "Mn":  # combining accent: "é" -> "e"
            continue
        if ch.isalnum() or category == "Mc":
            out.append(ch)
        elif ch in _APOSTROPHES or ch == ".":
            continue  # "Don't" -> "dont", "R.E.M." -> "rem"
        else:
            out.append(" ")
    return " ".join("".join(out).split())


def _normalize_or_keep(text: str) -> str:
    """normalize_text, except that all-punctuation names ("!!!", "÷") stay as they are."""
    return normalize_text(text) or text.strip().casefold()


def split_artists(raw: str) -> list[str]:
    """Split a credit line into normalized individual names, order preserved.

    Splitting is deliberately aggressive ("Simon & Garfunkel" becomes two names):
    both sides of a comparison are split the same way, so the result still matches,
    while "A, B" (Spotify) lines up with "A & B" (Apple Music).
    """
    raw = re.sub(r"[()\[\]]", " ", unicodedata.normalize("NFKC", raw))
    names: list[str] = []
    for part in _ARTIST_SEPARATOR.split(raw):
        name = _normalize_or_keep(part)
        if name.startswith("the ") and len(name) > 4:
            name = name[4:]
        if name and name not in names:
            names.append(name)
    return names


@dataclass(frozen=True)
class _Parsed:
    base: str
    words: tuple[str, ...]  # leftover qualifier words
    flags: frozenset[str]
    featured: tuple[str, ...]


def _split_qualifiers(raw: str) -> tuple[str, list[str]]:
    """Return (base, qualifiers): bracketed groups and " - " suffixes are qualifiers."""
    text = unicodedata.normalize("NFKC", raw).translate(_DASHES)
    qualifiers: list[str] = []
    while found := _BRACKETED.findall(text):  # loop handles nested brackets
        qualifiers += [round_ or square for round_, square in found]
        text = _BRACKETED.sub(" ", text)
    base, *suffixes = _DASH_SEPARATOR.split(text)
    return base, [q for q in suffixes + qualifiers if q.strip()]


def _parse(raw: str) -> _Parsed:
    base, qualifiers = _split_qualifiers(raw)
    featured: list[str] = []
    if match := _BARE_FEAT.search(base):
        featured += split_artists(match.group(1))
        base = base[: match.start()]

    flags: set[str] = set()
    words: list[str] = []
    for qualifier in qualifiers:
        if match := _QUALIFIER_FEAT.search(qualifier):
            featured += split_artists(match.group(1))
            qualifier = qualifier[: match.start()]
        text = normalize_text(qualifier)
        for flag, pattern in _FLAG_PATTERNS:
            text, hits = pattern.subn(" ", text)
            if hits:
                flags.add(flag)
        words += [w for w in text.split() if w not in _FILLER_WORDS and not _ORDINAL.fullmatch(w)]

    # A title that is nothing but a qualifier, e.g. "(Nice Dream)", keeps its text.
    base = normalize_text(base) or _normalize_or_keep(raw)
    return _Parsed(base, tuple(words), frozenset(flags), tuple(featured))


def normalize_track(
    title: str, artist: str, album: str = "", duration_ms: int | None = None
) -> NormalizedTrack:
    parsed_title = _parse(title)
    artists = split_artists(artist)
    artists += [name for name in parsed_title.featured if name not in artists]

    flags = set(parsed_title.flags)
    album_text = ""
    if album.strip():
        parsed_album = _parse(album)
        # Whether a track is a remaster is recorded in the title on one service and
        # in the album name on the other, so the album's marker counts for the track.
        if REMASTER in parsed_album.flags:
            flags.add(REMASTER)
        extra = sorted({*parsed_album.words, *(parsed_album.flags - {REMASTER})})
        album_text = " ".join([parsed_album.base, *extra])

    return NormalizedTrack(
        title=parsed_title.base,
        qualifiers=frozenset(parsed_title.words),
        flags=frozenset(flags),
        artists=tuple(artists),
        album=album_text,
        duration_ms=duration_ms,
    )


def source_key(track: SourceTrack) -> str:
    """Stable cache key: the Spotify track ID, else one built from normalized metadata."""
    if track.spotify_track_id:
        return f"spotify:track:{track.spotify_track_id}"
    n = normalize_track(track.title, track.artist, track.album, track.duration_ms)
    title = " ".join([n.title, *sorted(n.qualifiers), *sorted(n.flags)])
    seconds = "" if n.duration_ms is None else str(round(n.duration_ms / 1000))
    return f"meta:{'+'.join(n.artists)}|{title}|{n.album}|{seconds}"
