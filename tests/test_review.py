"""Manual review, driven by scripted answers instead of a keyboard."""

import pytest

from daily_mix_sync.apple_music import MockCatalog
from daily_mix_sync.config import Settings
from daily_mix_sync.database import MappingStore
from daily_mix_sync.importer import Playlist
from daily_mix_sync.matcher import MatchStatus
from daily_mix_sync.models import MANUAL, AppleCandidate, SourceTrack
from daily_mix_sync.review import review_results
from daily_mix_sync.sync import match_playlist

NUTSHELL = AppleCandidate("N1", "Nutshell (Unplugged Take)", "Alice In Chains", "Jar of Flies - EP", 259_000)
NUTSHELL_LIVE = AppleCandidate("N2", "Nutshell (Live)", "Alice In Chains", "MTV Unplugged (Live)", 297_000)
LET_IT_GO = AppleCandidate("L1", "Let It Go", "Idina Menzel", "Frozen", 223_840)
BRIGHTSIDE = AppleCandidate("A1", "Mr. Brightside", "The Killers", "Hot Fuss", 222_973)

WANT_NUTSHELL = SourceTrack("Nutshell", "Alice In Chains", "Jar of Flies", 259_000, "s1")
WANT_BRIGHTSIDE = SourceTrack("Mr. Brightside", "The Killers", "Hot Fuss", 222_000, "s2")
WANT_LET_IT_GO = SourceTrack('Let It Go - From "Frozen"', "Idina Menzel", "Frozen", 224_000, "s3")
PLAYLIST = Playlist("Daily Mix 1", (WANT_NUTSHELL, WANT_BRIGHTSIDE, WANT_LET_IT_GO))


@pytest.fixture
def catalog():
    return MockCatalog([NUTSHELL_LIVE, NUTSHELL, LET_IT_GO, BRIGHTSIDE])


@pytest.fixture
def store(tmp_path):
    with MappingStore(tmp_path / "mappings.sqlite3") as store:
        yield store


@pytest.fixture
def results(catalog, store):
    results = match_playlist(PLAYLIST, catalog, store, Settings())
    assert [r.status for r in results] == [
        MatchStatus.REVIEW, MatchStatus.MATCHED, MatchStatus.REVIEW,
    ]
    return results


class Answers:
    """Stands in for input(): hands out scripted answers, then signals end of input."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)


def run_review(results, store, *answers):
    shown = []
    ask = Answers(*answers)
    reviewed = review_results(results, store, ask=ask, show=shown.append)
    return reviewed, "\n".join(shown), ask


def test_picking_a_candidate_stores_a_manual_mapping(results, store):
    reviewed, _, _ = run_review(results, store, "1", "1")
    nutshell = reviewed[0]
    assert nutshell.status is MatchStatus.MANUAL
    assert nutshell.chosen == NUTSHELL
    mapping = store.get("spotify:track:s1")
    assert (mapping.persistent_id, mapping.method, mapping.score) == ("N1", MANUAL, 80.0)
    assert nutshell.mapping == mapping
    assert reviewed[2].chosen == LET_IT_GO
    assert reviewed[1] is results[1]  # the automatic match was not asked about


def test_a_lower_ranked_candidate_can_be_picked(results, store):
    reviewed, _, _ = run_review(results, store, "2", "s")
    assert reviewed[0].chosen == NUTSHELL_LIVE
    assert store.get("spotify:track:s1").persistent_id == "N2"


def test_the_question_shows_source_and_candidates(results, store):
    _, shown, ask = run_review(results, store, "s", "s")
    assert "Needs review (1 of 2)" in shown and "Needs review (2 of 2)" in shown
    assert "Source:\n   Nutshell\n   Alice In Chains\n   Jar of Flies\n   4:19" in shown
    assert " 1. Nutshell (Unplugged Take)\n    Alice In Chains\n    Jar of Flies - EP\n    4:19" in shown
    assert "    Score: 80.0  (" in shown and "qualifier mismatch" in shown
    assert " 2. Nutshell (Live)" in shown and "version mismatch: live" in shown
    assert " s. Skip\n q. Quit review" in shown
    assert ask.prompts == ["Selection: ", "Selection: "]


@pytest.mark.parametrize("skip", ["s", "S", "skip", "", "  "])
def test_skipping_leaves_the_track_unresolved_and_stores_nothing(results, store, skip):
    reviewed, _, _ = run_review(results, store, skip, skip)
    assert [r.status for r in reviewed] == [r.status for r in results]
    assert reviewed[0].chosen is None
    assert store.get("spotify:track:s1") is None
    assert store.count() == 1  # only the automatic match


@pytest.mark.parametrize("quit_answer", ["q", "Q", "quit"])
def test_quitting_stops_asking_but_keeps_earlier_answers(results, store, quit_answer):
    reviewed, _, ask = run_review(results, store, "1", quit_answer)
    assert reviewed[0].status is MatchStatus.MANUAL
    assert reviewed[2].status is MatchStatus.REVIEW
    assert len(ask.prompts) == 2

    again, _, ask = run_review(results, store, quit_answer)
    assert len(ask.prompts) == 1  # the second question was never asked
    assert again[0].status is MatchStatus.REVIEW


def test_end_of_input_counts_as_quitting(results, store):
    reviewed, _, ask = run_review(results, store)  # no answers at all
    assert [r.status for r in reviewed] == [r.status for r in results]
    assert len(ask.prompts) == 1


@pytest.mark.parametrize("nonsense", ["0", "3", "99", "x", "1.5", "-1", "first"])
def test_an_unusable_answer_is_asked_again_never_guessed(results, store, nonsense):
    reviewed, shown, ask = run_review(results, store, nonsense, "2", "s")
    assert "Enter a number from 1 to 2, s to skip, or q to quit." in shown
    assert reviewed[0].chosen == NUTSHELL_LIVE
    assert ask.prompts.count("Selection: ") == 3


def test_nothing_to_review_asks_nothing(catalog, store):
    only_auto = match_playlist(Playlist("Mix", (WANT_BRIGHTSIDE,)), catalog, store, Settings())
    reviewed, shown, ask = run_review(only_auto, store, "1")
    assert reviewed == only_auto and shown == "" and ask.prompts == []


def test_a_manual_choice_is_remembered_and_not_asked_again(results, catalog, store):
    run_review(results, store, "2", "s")
    later = match_playlist(PLAYLIST, catalog, store, Settings())
    assert later[0].status is MatchStatus.CACHED
    assert later[0].chosen == NUTSHELL_LIVE and later[0].mapping.is_manual
    _, _, ask = run_review(later, store, "s")
    assert len(ask.prompts) == 1  # only the one that was skipped is asked again


def test_a_manual_choice_survives_a_better_automatic_candidate_appearing(results, store):
    run_review(results, store, "2", "s")
    exact = AppleCandidate("N9", "Nutshell", "Alice In Chains", "Jar of Flies", 259_000)
    bigger = MockCatalog([exact, NUTSHELL_LIVE, NUTSHELL, LET_IT_GO, BRIGHTSIDE])
    later = match_playlist(PLAYLIST, bigger, store, Settings())
    assert later[0].chosen == NUTSHELL_LIVE
    assert store.get("spotify:track:s1").method == MANUAL
