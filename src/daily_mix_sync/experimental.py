"""EXPERIMENTAL commands: the Apple Music catalog, through the Music window.

None of this is part of the supported workflow. `sync`, `match` and `review` use only
songs already in the Music library and never touch this module, `catalog.py` or
`music_ui.py`. The commands here are kept for possible future work on songs the
library does not have.

What sets them apart from everything else in the tool:

- they operate the Music window through macOS Accessibility, so they need that
  permission and take over the screen while they run;
- `experimental-catalog-add-test` and `experimental-catalog-fill` ADD SONGS TO THE
  MUSIC LIBRARY, and nothing here can take a song out again.

Every command name starts with "experimental-", and the ones that add to the library
ask first.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from . import cli, review
from .catalog import CatalogLookup, add_and_confirm, look_up, resolve_missing
from .database import MappingStore
from .importer import load_playlist
from .matcher import MatchStatus, ScoredCandidate
from .models import SourceTrack
from .music_ui import MusicCatalogUI
from .sync import match_playlist, search_term

WARNING = (
    "EXPERIMENTAL, not part of normal sync. This operates the Music window and can add "
    "songs to your Music library; the tool cannot remove them again."
)


def _catalog_ui() -> MusicCatalogUI:
    return MusicCatalogUI()


def choose_catalog_result(
    lookup: CatalogLookup,
    ask: Callable[[str], str] | None = None,
    show: Callable[[str], None] | None = None,
) -> ScoredCandidate | str:
    """Ask which Apple Music catalog result to add for a track; "skip" or "quit" otherwise.

    Picking a result authorises adding that one song to the library. Nothing is
    remembered here: the mapping is stored only once the song is in the library.
    """
    ask = input if ask is None else ask
    show = print if show is None else show
    track = lookup.track
    show("\nApple Music catalog match required\n")
    show("Source:")
    for line in (track.title, track.artist, track.album or "(album unknown)"):
        show(f"   {line}")
    show(f"   {cli._clock(track.duration_ms)}")
    show("\nCatalog results (the results page shows no album or duration):")
    for index, scored in enumerate(lookup.match.candidates, start=1):
        row = lookup.row(scored)
        show(f"\n{index:2d}. {row.title}")
        show(f"    {row.artist}")
        show(f"    Score: {scored.score:.1f}  ({scored.explain()})")
    show("\n s. Skip")
    show(" q. Stop catalog resolution\n")
    return review.ask_selection(lookup.match.candidates, ask, show)


def _confirmed(question: str, args: argparse.Namespace, what: str) -> bool:
    """Ask before adding to the library, unless --yes was given."""
    if args.yes:
        return True
    if not cli._interactive():
        print(
            f"error: not confirmed. {what} needs a yes at the prompt, or --yes when "
            "there is no one to ask.",
            file=sys.stderr,
        )
        return False
    try:
        answer = input(f"\n{question} [y/N] ")
    except EOFError:
        answer = ""
    if answer.strip().lower() in ("y", "yes"):
        return True
    print("Nothing was added.")
    return False


def _print_lookup(lookup: CatalogLookup, accept: float) -> None:
    track = lookup.track
    print("Apple Music catalog search\n")
    print(f"Looking for: {cli._describe_source(track)}")
    print(f"Query:       {search_term(track)}\n")
    if not lookup.match.candidates:
        print("Results: none")
    else:
        print("Results, best match first (the results page shows no album or duration):")
    for scored in lookup.match.candidates:
        row = lookup.row(scored)
        print(f"  {scored.score:5.1f}  {row.title} — {row.artist}   (result {row.ordinal} on the page)")
        print(f"         {scored.explain()}")
    best = lookup.match.best
    if best is None:
        print("\nResult: nothing found.")
    elif lookup.match.status is MatchStatus.MATCHED:
        row = lookup.row(best)
        print(f"\nResult: {row.title} — {row.artist} would be chosen (score {best.score:.1f}; accepted from {accept:g}).")
    elif lookup.match.status is MatchStatus.REVIEW:
        print(f"\nResult: needs a choice by hand (best score {best.score:.1f}; accepted from {accept:g}).")
    else:
        print(f"\nResult: no acceptable match (best score {best.score:.1f}; accepted from {accept:g}).")


def _cmd_ui_inspect(args: argparse.Namespace) -> int:
    ui = _catalog_ui()
    ui.require_accessibility()
    with ui.session():
        report = ui.inspect()
        dumps = [(part, ui.dump(part)) for part in ("toolbar", "pane")] if args.dump else []
    print("What the catalog automation relies on in the Music window:\n")
    for state, item, detail in report:
        mark = {"ok": "ok     ", "missing": "MISSING", "info": "note   "}.get(state, state)
        print(f"  {mark}  {item}: {detail}")
    for part, text in dumps:
        elements = [e.strip(" ,") for e in text.split(" of window Music of application process Music")]
        print(f"\nEvery element under the {part} ({len([e for e in elements if e])}):")
        for element in elements:
            if element:
                print(f"  {element}")
    missing = [item for state, item, _ in report if state == "missing"]
    if missing:
        print(f"\n{len(missing)} expected part(s) not found. Catalog search will not work until "
              "music_ui.py is brought in line with this layout.")
        return 1
    print("\nEverything the catalog automation needs is in place.")
    return 0


def _cmd_catalog_search(args: argparse.Namespace) -> int:
    settings = cli.load_settings(args.config)
    ui = _catalog_ui()
    ui.require_accessibility()
    track = SourceTrack(args.title, args.artist, args.album or "", args.duration)
    with ui.session():
        lookup = look_up(track, ui, settings)
    _print_lookup(lookup, settings.matching.auto_accept_threshold)
    print("Nothing was changed.")
    return 0 if lookup.match.status is MatchStatus.MATCHED else 1


def _cmd_catalog_add_test(args: argparse.Namespace) -> int:
    settings, music = cli._music(args)
    ui = _catalog_ui()
    ui.require_accessibility()
    print(WARNING + "\n")
    track = SourceTrack(args.title, args.artist, args.album or "", args.duration)
    with ui.session():
        lookup = look_up(track, ui, settings)
    _print_lookup(lookup, settings.matching.auto_accept_threshold)

    by_hand = False
    picked = lookup.match.best
    if lookup.match.status is MatchStatus.REVIEW and cli._interactive():
        answer = choose_catalog_result(lookup)
        picked, by_hand = (answer, True) if isinstance(answer, ScoredCandidate) else (None, False)
    elif lookup.match.status is not MatchStatus.MATCHED:
        picked = None
    if picked is None:
        print("Nothing was added.")
        return 1
    row = lookup.row(picked)
    question = f"Add {row.title!r} by {row.artist!r} to your Music library?"
    if not _confirmed(question, args, "Adding a song to your library"):
        return 1

    with ui.session():
        found, added = add_and_confirm(track, lookup, picked, by_hand, music, ui, settings)
    print()
    print("Add to Library was chosen in Music." if added else
          "Music shows this song as already in your library; nothing was added.")
    if found is None:
        print(
            f"error: no matching track showed up in the library within "
            f"{settings.catalog_wait_s} seconds.",
            file=sys.stderr,
        )
        return 1
    album = f" [{found.album}]" if found.album else ""
    print(f"The library now has: {found.title} — {found.artist}{album} {cli._clock(found.duration_ms)}")
    print(f"Persistent ID:       {found.persistent_id}")
    print("It was not added to any playlist.")
    return 0


def _cmd_catalog_fill(args: argparse.Namespace) -> int:
    """Add to the library, from the catalog, the songs of an export that it lacks."""
    settings, music = cli._music(args)
    playlist = load_playlist(args.playlist)
    ui = _catalog_ui()
    ui.require_accessibility()
    print(WARNING + "\n")

    with MappingStore(args.db or settings.database_path) as store:
        results = match_playlist(playlist, music, store, settings)
        missing = [r.track for r in results if r.status is MatchStatus.FAILED]
        print(f"{playlist.name}: {len(missing)} of {len(playlist.tracks)} track(s) are not in your library.")
        if not missing:
            print("Nothing to look for.")
            return 0
        for track in missing:
            print(f"  - {track.title} — {track.artist}")
        if not args.dry_run:
            question = (
                f"Look for these {len(missing)} in the Apple Music catalog and add the matches "
                "to your Music library?"
            )
            if not _confirmed(question, args, "Adding songs to your library"):
                return 1

        def choose(lookup: CatalogLookup) -> ScoredCandidate | str:
            ui.hand_back()  # the question is asked in the terminal, not in Music
            return choose_catalog_result(lookup)

        ask = not args.dry_run and not args.no_review and cli._interactive()
        print(
            "\nSearching the Apple Music catalog. Music will come to the front; please leave "
            "the Mac alone until it hands back.",
            flush=True,
        )
        with ui.session():
            _, report = resolve_missing(
                results, music, ui, store, settings,
                dry_run=args.dry_run, choose=choose if ask else None,
            )

    if report.added:
        print(f"\nAdded to your library ({len(report.added)}):")
        for track, found in report.added:
            print(f"  {track.title} — {track.artist}  →  {found.title} — {found.artist}")
    if report.would_add:
        print(f"\nWould add to your library ({len(report.would_add)}):")
        for track, row in report.would_add:
            print(f"  {track.title} — {track.artist}  →  {row.title} — {row.artist}")
    if report.problems:
        print(f"\nNot added ({len(report.problems)}):")
        for track, reason in report.problems:
            print(f"  {track.title} — {track.artist}\n      {reason}")
    if report.stopped:
        print(f"\nThe catalog step ended early: {report.stopped}")
    if args.dry_run:
        print("\nNo changes made (--dry-run).")
    elif report.added:
        print(f"\nThey are in your library now. To put them in the playlist: sync {args.playlist}")
    else:
        print("\nNothing was added.")
    return 0


def add_commands(
    commands: argparse._SubParsersAction,
    common: argparse.ArgumentParser,
    track: argparse.ArgumentParser,
) -> None:
    """Register the experimental commands. They are the only way into this module."""
    inspect = commands.add_parser(
        "experimental-ui-inspect", parents=[common],
        help="EXPERIMENTAL: check that the Music window has what catalog search relies on",
    )
    inspect.add_argument(
        "--dump", action="store_true", help="also list every element of the toolbar and main pane"
    )
    inspect.set_defaults(handler=_cmd_ui_inspect)

    search = commands.add_parser(
        "experimental-catalog-search", parents=[common, track],
        help="EXPERIMENTAL: search the Apple Music catalog for one song (changes nothing)",
    )
    search.set_defaults(handler=_cmd_catalog_search)

    add_test = commands.add_parser(
        "experimental-catalog-add-test", parents=[common, track],
        help="EXPERIMENTAL: find one song in the Apple Music catalog and ADD IT TO YOUR LIBRARY",
    )
    add_test.add_argument("--yes", action="store_true", help="add without asking for confirmation")
    add_test.set_defaults(handler=_cmd_catalog_add_test)

    fill = commands.add_parser(
        "experimental-catalog-fill", parents=[common],
        help="EXPERIMENTAL: ADD TO YOUR LIBRARY the songs of an export that it lacks",
    )
    fill.add_argument("playlist", type=Path, help="playlist export (JSON)")
    fill.add_argument(
        "--dry-run", action="store_true", help="search and list what would be added; add nothing"
    )
    fill.add_argument("--yes", action="store_true", help="add without asking for confirmation")
    fill.add_argument(
        "--no-review", action="store_true",
        help="do not ask about ambiguous catalog results; leave them out",
    )
    fill.add_argument("--db", type=Path, metavar="FILE", help="mapping database")
    fill.set_defaults(handler=_cmd_catalog_fill)
