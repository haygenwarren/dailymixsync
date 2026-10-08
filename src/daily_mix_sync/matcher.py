"""Score Apple Music candidates against a source track and decide what to do with it.

score = weighted average of title / artist / album / duration similarity (0-100)
        minus penalties for version mismatches

A component that cannot be computed (album or duration missing on either side) is
left out and the remaining weights are rescaled, so missing metadata neither helps
nor hurts a candidate.
"""

from __future__ import annotations

import enum
from collections.abc import Iterable
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from . import normalize
from .models import AppleCandidate, Mapping, SourceTrack
from .normalize import NormalizedTrack, normalize_track

DEFAULT_FLAG_PENALTIES = {
    normalize.LIVE: 30.0,
    normalize.REMIX: 30.0,
    normalize.ACOUSTIC: 30.0,
    normalize.DEMO: 30.0,
    normalize.INSTRUMENTAL: 30.0,
    normalize.RADIO_EDIT: 15.0,
    # Same recording, different mastering: only enough to break a tie.
    normalize.REMASTER: 2.0,
}

# Artist credits often differ only in how many collaborators are listed
# ("A, B" vs "A"). Coverage of the shorter list dominates; the longer list's
# unmatched names cost a little, so an exact credit still beats a partial one.
_SHORTER_LIST_WEIGHT = 0.7


@dataclass(frozen=True)
class MatchConfig:
    auto_accept_threshold: float = 90.0  # score >= this: accept without asking
    review_threshold: float = 75.0  # score >= this: offer for manual review
    weight_title: float = 0.45
    weight_artist: float = 0.35
    weight_album: float = 0.10
    weight_duration: float = 0.10
    duration_tolerance_s: float = 3.0  # differences up to this count as identical
    duration_zero_s: float = 15.0  # duration similarity falls to 0 at this difference
    duration_mismatch_s: float = 30.0  # "materially different": extra penalty from here
    duration_mismatch_penalty: float = 15.0
    qualifier_mismatch_penalty: float = 20.0  # e.g. "(Taylor's Version)" vs nothing
    # Flags left out here keep their default penalty.
    flag_penalties: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "flag_penalties", {**DEFAULT_FLAG_PENALTIES, **self.flag_penalties}
        )
        if self.review_threshold > self.auto_accept_threshold:
            raise ValueError("review_threshold must not exceed auto_accept_threshold")
        weights = (self.weight_title, self.weight_artist, self.weight_album, self.weight_duration)
        if min(weights) < 0 or self.weight_title + self.weight_artist <= 0:
            raise ValueError("weights must be >= 0, and title/artist weights must not both be 0")
        if self.duration_tolerance_s >= self.duration_zero_s:
            raise ValueError("duration_tolerance_s must be smaller than duration_zero_s")
        unknown = set(self.flag_penalties) - normalize.FLAGS
        if unknown:
            raise ValueError(
                f"unknown flag_penalties keys {sorted(unknown)}; known: {sorted(normalize.FLAGS)}"
            )


class MatchStatus(enum.Enum):
    CACHED = "cached"  # reused a stored mapping whose track still exists
    MATCHED = "matched"  # best candidate accepted automatically
    MANUAL = "manual"  # candidate picked by hand during review, in this run
    REVIEW = "review"  # plausible candidate, needs a human decision
    FAILED = "failed"  # no search results, or nothing scored high enough


@dataclass(frozen=True)
class ScoredCandidate:
    candidate: AppleCandidate
    score: float
    title_score: float
    artist_score: float
    album_score: float | None  # None: not comparable, weight redistributed
    duration_score: float | None
    penalties: tuple[tuple[str, float], ...] = ()

    def explain(self) -> str:
        def show(value: float | None) -> str:
            return "n/a" if value is None else f"{value:.0f}"

        text = (
            f"title {show(self.title_score)}, artist {show(self.artist_score)}, "
            f"album {show(self.album_score)}, duration {show(self.duration_score)}"
        )
        for reason, points in self.penalties:
            text += f"; -{points:.1f} {reason}"
        return text


@dataclass(frozen=True)
class MatchResult:
    track: SourceTrack
    status: MatchStatus
    candidates: tuple[ScoredCandidate, ...] = ()  # best first
    mapping: Mapping | None = None  # set when status is CACHED or MANUAL
    chosen: AppleCandidate | None = None  # the track to use; None while unresolved
    stale: bool = False  # a stored mapping pointed at a track that no longer exists
    # How the candidates were found. Set by the search step, not by the matcher.
    title_search: str = ""  # the title-only query, when that broader search was run
    title_search_ids: frozenset[str] = frozenset()  # candidates only that search found

    @property
    def best(self) -> ScoredCandidate | None:
        return self.candidates[0] if self.candidates else None


def _artist_similarity(a: tuple[str, ...], b: tuple[str, ...]) -> float:
    if not a or not b:
        return 0.0

    def coverage(names: tuple[str, ...], others: tuple[str, ...]) -> float:
        return sum(max(fuzz.ratio(n, o) for o in others) for n in names) / len(names)

    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    return _SHORTER_LIST_WEIGHT * coverage(shorter, longer) + (
        1 - _SHORTER_LIST_WEIGHT
    ) * coverage(longer, shorter)


def _duration_similarity(diff_s: float, config: MatchConfig) -> float:
    if diff_s <= config.duration_tolerance_s:
        return 100.0
    if diff_s >= config.duration_zero_s:
        return 0.0
    span = config.duration_zero_s - config.duration_tolerance_s
    return 100.0 * (config.duration_zero_s - diff_s) / span


def _score(src: NormalizedTrack, candidate: AppleCandidate, config: MatchConfig) -> ScoredCandidate:
    cand = normalize_track(
        candidate.title, candidate.artist, candidate.album, candidate.duration_ms
    )
    title_score = fuzz.ratio(src.title, cand.title)
    artist_score = _artist_similarity(src.artists, cand.artists)
    album_score = fuzz.ratio(src.album, cand.album) if src.album and cand.album else None
    duration_diff_s = None
    if src.duration_ms is not None and cand.duration_ms is not None:
        duration_diff_s = abs(src.duration_ms - cand.duration_ms) / 1000
    duration_score = (
        None if duration_diff_s is None else _duration_similarity(duration_diff_s, config)
    )

    parts = [(config.weight_title, title_score), (config.weight_artist, artist_score)]
    if album_score is not None:
        parts.append((config.weight_album, album_score))
    if duration_score is not None:
        parts.append((config.weight_duration, duration_score))
    base = sum(weight * value for weight, value in parts) / sum(weight for weight, _ in parts)

    penalties: list[tuple[str, float]] = []
    for flag in sorted(src.flags ^ cand.flags):
        if points := config.flag_penalties[flag]:
            penalties.append((f"version mismatch: {flag}", points))
    differing = src.qualifiers ^ cand.qualifiers
    if differing:
        share = len(differing) / len(src.qualifiers | cand.qualifiers)
        penalties.append(
            (
                f"qualifier mismatch: {' '.join(sorted(differing))}",
                config.qualifier_mismatch_penalty * share,
            )
        )
    if duration_diff_s is not None and duration_diff_s >= config.duration_mismatch_s:
        penalties.append(
            (f"duration differs by {duration_diff_s:.0f}s", config.duration_mismatch_penalty)
        )

    score = max(0.0, base - sum(points for _, points in penalties))
    return ScoredCandidate(
        candidate=candidate,
        score=round(score, 2),
        title_score=title_score,
        artist_score=artist_score,
        album_score=album_score,
        duration_score=duration_score,
        penalties=tuple(penalties),
    )


def _normalize_source(track: SourceTrack) -> NormalizedTrack:
    return normalize_track(track.title, track.artist, track.album, track.duration_ms)


def score_candidate(
    track: SourceTrack, candidate: AppleCandidate, config: MatchConfig | None = None
) -> ScoredCandidate:
    return _score(_normalize_source(track), candidate, config or MatchConfig())


def match_track(
    track: SourceTrack, candidates: Iterable[AppleCandidate], config: MatchConfig | None = None
) -> MatchResult:
    """Rank candidates and classify the track by its best candidate's score."""
    config = config or MatchConfig()
    src = _normalize_source(track)
    # sorted() is stable: equal scores keep the catalog's own search ranking.
    scored = tuple(
        sorted((_score(src, c, config) for c in candidates), key=lambda s: -s.score)
    )
    if not scored or scored[0].score < config.review_threshold:
        status = MatchStatus.FAILED
    elif scored[0].score < config.auto_accept_threshold:
        status = MatchStatus.REVIEW
    else:
        status = MatchStatus.MATCHED
    chosen = scored[0].candidate if status is MatchStatus.MATCHED else None
    return MatchResult(track=track, status=status, candidates=scored, chosen=chosen)
