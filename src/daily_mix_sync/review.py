"""Manual review: a person picks among the candidates the matcher would not decide on."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace

from .database import MappingStore
from .matcher import MatchResult, MatchStatus, ScoredCandidate
from .models import MANUAL


def _clock(duration_ms: int | None) -> str:
    if duration_ms is None:
        return "-:--"
    minutes, seconds = divmod(round(duration_ms / 1000), 60)
    return f"{minutes}:{seconds:02d}"


def _show_question(
    result: MatchResult, number: int, total: int, show: Callable[[str], None]
) -> None:
    track = result.track
    show(f"\nNeeds review ({number} of {total})\n")
    show("Source:")
    for line in (track.title, track.artist, track.album or "(album unknown)"):
        show(f"   {line}")
    show(f"   {_clock(track.duration_ms)}")
    show("\nCandidates:")
    for index, scored in enumerate(result.candidates, start=1):
        c = scored.candidate
        show(f"\n{index:2d}. {c.title}")
        for line in (c.artist, c.album or "(album unknown)", _clock(c.duration_ms)):
            show(f"    {line}")
        show(f"    Score: {scored.score:.1f}  ({scored.explain()})")
    show("\n s. Skip")
    show(" q. Quit review\n")


def ask_selection(
    candidates: Sequence[ScoredCandidate], ask: Callable[[str], str], show: Callable[[str], None]
) -> ScoredCandidate | str:
    """Return the picked candidate, or "skip" or "quit"."""
    while True:
        try:
            answer = ask("Selection: ").strip().lower()
        except EOFError:
            return "quit"  # input ended: stop asking rather than guess
        if answer in ("s", "skip", ""):
            return "skip"
        if answer in ("q", "quit"):
            return "quit"
        if answer.isdigit() and 1 <= int(answer) <= len(candidates):
            return candidates[int(answer) - 1]
        show(f"Enter a number from 1 to {len(candidates)}, s to skip, or q to quit.")


def review_results(
    results: Sequence[MatchResult],
    store: MappingStore,
    ask: Callable[[str], str] | None = None,
    show: Callable[[str], None] | None = None,
) -> list[MatchResult]:
    """Ask about every track that needs review; return the results with answers applied.

    A pick is stored as a manual mapping, so it is not asked again and outranks any
    automatic match. A skipped track stays as it was and will be asked about again.
    `ask` and `show` default to the keyboard and the screen.
    """
    ask = input if ask is None else ask
    show = print if show is None else show
    reviewed = list(results)
    pending = [i for i, result in enumerate(reviewed) if result.status is MatchStatus.REVIEW]
    for number, index in enumerate(pending, start=1):
        result = reviewed[index]
        _show_question(result, number, len(pending), show)
        answer = ask_selection(result.candidates, ask, show)
        if answer == "quit":
            break
        if isinstance(answer, ScoredCandidate):
            mapping = store.save(result.track, answer.candidate, answer.score, MANUAL)
            reviewed[index] = replace(
                result, status=MatchStatus.MANUAL, chosen=answer.candidate, mapping=mapping
            )
    return reviewed
