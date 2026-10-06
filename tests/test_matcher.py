import pytest

from daily_mix_sync.matcher import MatchConfig, MatchStatus, match_track, score_candidate
from daily_mix_sync.models import AppleCandidate, SourceTrack


def src(title="Song", artist="Artist", album="Album", seconds=200) -> SourceTrack:
    return SourceTrack(title, artist, album, None if seconds is None else seconds * 1000)


def cand(title="Song", artist="Artist", album="Album", seconds=200, id="1") -> AppleCandidate:
    return AppleCandidate(id, title, artist, album, None if seconds is None else seconds * 1000)


def score(source: SourceTrack, candidate: AppleCandidate, **config) -> float:
    return score_candidate(source, candidate, MatchConfig(**config)).score


def status(source: SourceTrack, *candidates: AppleCandidate, **config) -> MatchStatus:
    return match_track(source, candidates, MatchConfig(**config)).status


def penalty_reasons(source: SourceTrack, candidate: AppleCandidate) -> list[str]:
    return [reason for reason, _ in score_candidate(source, candidate).penalties]


# --- the same recording, described differently ----------------------------


def test_identical_track_scores_100():
    assert score(src(), cand()) == 100
    assert status(src(), cand()) is MatchStatus.MATCHED


@pytest.mark.parametrize(
    ("source_title", "candidate_title"),
    [
        ("KILLING IN THE NAME", "Killing In the Name"),
        ("Don't Stop Me Now", "Don’t Stop Me Now"),
        ("Déjà Vu", "Deja Vu"),
        ("Mr. Brightside", "Mr Brightside"),
        ("Rock & Roll", "Rock and Roll"),
        ("What's Up?", "What's  Up"),
    ],
)
def test_capitalization_punctuation_and_accents_do_not_matter(source_title, candidate_title):
    assert score(src(source_title), cand(candidate_title)) == 100


@pytest.mark.parametrize(
    ("candidate_title", "candidate_artist"),
    [
        ("Song (feat. Guest)", "Main"),
        ("Song [feat. Guest]", "Main"),
        ("Song", "Main & Guest"),
        ("Song", "Main feat. Guest"),
        ("Song (with Guest)", "Main"),
    ],
)
def test_featured_artist_credit_styles_are_equivalent(candidate_title, candidate_artist):
    source = src("Song", "Main, Guest")
    assert score(source, cand(candidate_title, candidate_artist)) == 100


def test_uncredited_guest_still_matches_but_scores_below_exact_credit():
    source = src("Song", "Main, Guest")
    partial = score(source, cand("Song", "Main"))
    assert 90 <= partial < 100


@pytest.mark.parametrize(
    ("candidate_title", "candidate_album"),
    [
        ("Song (Remastered)", "Album"),
        ("Song", "Album (2011 Remaster)"),
        ("Song (2011 Remaster)", "Album (Deluxe Edition) [2011 Remaster]"),
    ],
)
def test_remaster_wording_does_not_matter(candidate_title, candidate_album):
    source = src("Song - Remastered 2011", album="Album (2011 Remaster)")
    assert score(source, cand(candidate_title, album=candidate_album)) == 100


def test_remaster_against_unmarked_release_is_a_small_penalty():
    source = src("Song - Remastered 2011")
    assert score(source, cand("Song")) == 98
    assert penalty_reasons(source, cand("Song")) == ["version mismatch: remaster"]


def test_edition_suffixes_do_not_matter():
    source = src("Song", album="Album (Deluxe Edition)")
    assert score(source, cand("Song (Album Version)", album="Album - Single")) == 100


# --- a different recording of the same song -------------------------------


@pytest.mark.parametrize(
    ("variant", "flag"),
    [
        ("Song (Live)", "live"),
        ("Song (Acoustic)", "acoustic"),
        ("Song (Acoustic Version)", "acoustic"),
        ("Song (Remix)", "remix"),
        ("Song (Demo)", "demo"),
        ("Song (Instrumental)", "instrumental"),
    ],
)
def test_version_mismatch_is_rejected_in_both_directions(variant, flag):
    # Everything else is identical, so the penalty alone has to do the work.
    assert score(src("Song"), cand(variant)) == 70
    assert score(src(variant), cand("Song")) == 70
    assert status(src("Song"), cand(variant)) is MatchStatus.FAILED
    assert penalty_reasons(src("Song"), cand(variant)) == [f"version mismatch: {flag}"]


@pytest.mark.parametrize("variant", ["Song (Live)", "Song - Acoustic", "Song (Remix)"])
def test_same_version_on_both_sides_matches(variant):
    assert score(src(variant), cand(variant)) == 100


def test_spotify_dash_style_equals_apple_parenthesis_style():
    assert score(src("Song - Live"), cand("Song (Live)")) == 100
    assert score(src("Song - Acoustic Version"), cand("Song (Acoustic)")) == 100
    assert score(src("Song - Radio Edit"), cand("Song (Radio Edit)")) == 100


def test_studio_version_wins_over_live_version_listed_first():
    result = match_track(
        src("Song"),
        [cand("Song (Live)", album="Live in Concert", seconds=262, id="live"), cand(id="studio")],
    )
    assert result.status is MatchStatus.MATCHED
    assert result.best.candidate.persistent_id == "studio"


def test_live_source_picks_the_live_candidate():
    result = match_track(src("Song - Live"), [cand(id="studio"), cand("Song (Live)", id="live")])
    assert result.best.candidate.persistent_id == "live"


def test_different_remixes_are_not_matched_automatically():
    source = src("Song (Skrillex Remix)")
    other = cand("Song (Tiësto Remix)")
    assert status(source, other) is MatchStatus.REVIEW
    assert status(source, cand("Song (Skrillex Remix)")) is MatchStatus.MATCHED


@pytest.mark.parametrize(
    "variant",
    ["Song (Taylor's Version)", "Song (Sped Up)", 'Song (From "The Movie")', "Song (Pt. 2)"],
)
def test_unrecognised_qualifier_on_one_side_needs_review(variant):
    assert score(src("Song"), cand(variant)) == 80
    assert status(src("Song"), cand(variant)) is MatchStatus.REVIEW
    assert status(src(variant), cand("Song")) is MatchStatus.REVIEW


def test_numbered_parts_are_not_matched_automatically():
    assert status(src("Song (Pt. 1)"), cand("Song (Pt. 2)")) is MatchStatus.REVIEW


# --- duration -------------------------------------------------------------


@pytest.mark.parametrize("seconds", [198, 199, 200, 201, 203])
def test_small_duration_differences_are_ignored(seconds):
    assert score(src(seconds=200), cand(seconds=seconds)) == 100


def test_duration_similarity_fades_between_tolerance_and_zero():
    nine_off = score_candidate(src(seconds=200), cand(seconds=209))
    assert nine_off.duration_score == 50
    assert nine_off.score == 95
    assert score_candidate(src(seconds=200), cand(seconds=215)).duration_score == 0


def test_materially_different_duration_is_penalised():
    source, long_cut = src(seconds=200), cand(seconds=260)
    assert score(source, long_cut) == 75  # 100 - 10 (duration weight) - 15 (penalty)
    assert penalty_reasons(source, long_cut) == ["duration differs by 60s"]
    assert status(source, long_cut) is not MatchStatus.MATCHED


def test_missing_album_and_duration_are_left_out_not_counted_against():
    result = score_candidate(src(album="", seconds=None), cand())
    assert result.score == 100
    assert result.album_score is None
    assert result.duration_score is None


def test_missing_metadata_does_not_hide_a_wrong_artist():
    assert status(src(album="", seconds=None), cand(artist="Somebody Else")) is MatchStatus.FAILED


# --- different songs ------------------------------------------------------


def test_unrelated_song_with_the_same_title_is_rejected():
    source = SourceTrack("Dreams", "Fleetwood Mac", "Rumours", 257_000)
    cranberries = AppleCandidate(
        "1", "Dreams", "The Cranberries", "Everybody Else Is Doing It, So Why Can't We?", 271_000
    )
    assert status(source, cranberries) is MatchStatus.FAILED


def test_same_title_same_length_same_album_name_still_needs_the_artist():
    assert status(src(), cand(artist="Somebody Else")) is not MatchStatus.MATCHED


def test_same_artist_different_song_is_rejected():
    assert status(src("Yesterday"), cand("Help!")) is MatchStatus.FAILED


def test_correct_artist_is_picked_among_same_titled_songs():
    source = SourceTrack("Dreams", "Fleetwood Mac", "Rumours", 257_000)
    result = match_track(
        source,
        [
            AppleCandidate("cranberries", "Dreams", "The Cranberries", "Everybody Else", 271_000),
            AppleCandidate("cover", "Dreams", "The Corrs", "Talk on Corners", 241_000),
            AppleCandidate("right", "Dreams", "Fleetwood Mac", "Rumours (Deluxe Edition)", 258_000),
        ],
    )
    assert result.status is MatchStatus.MATCHED
    assert result.best.candidate.persistent_id == "right"
    assert [c.candidate.persistent_id for c in result.candidates][0] == "right"
    assert result.candidates[0].score > result.candidates[1].score


# --- decisions and configuration ------------------------------------------


def test_no_candidates_fails():
    result = match_track(src(), [])
    assert result.status is MatchStatus.FAILED
    assert result.best is None


def test_equal_scores_keep_catalog_order():
    result = match_track(src(), [cand(id="first"), cand(id="second")])
    assert [c.candidate.persistent_id for c in result.candidates] == ["first", "second"]


def test_default_thresholds():
    assert status(src(seconds=200), cand(seconds=215)) is MatchStatus.MATCHED  # exactly 90
    assert status(src("Song"), cand("Song (Sped Up)")) is MatchStatus.REVIEW  # 80
    assert status(src("Song"), cand("Song (Live)")) is MatchStatus.FAILED  # 70


def test_thresholds_are_configurable():
    source, candidate = src("Song"), cand("Song (Sped Up)")  # scores 80
    assert status(source, candidate, auto_accept_threshold=80) is MatchStatus.MATCHED
    assert status(source, candidate, review_threshold=85) is MatchStatus.FAILED


def test_weights_are_configurable():
    source, candidate = src(album="Album"), cand(album="Completely Different")
    assert score(source, candidate) < 100
    assert score(source, candidate, weight_album=0) == 100


def test_penalties_are_configurable():
    assert score(src("Song"), cand("Song (Live)"), flag_penalties={"live": 5}) == 95
    assert score(src("Song"), cand("Song (Sped Up)"), qualifier_mismatch_penalty=0) == 100


@pytest.mark.parametrize(
    "bad",
    [
        {"review_threshold": 95, "auto_accept_threshold": 90},
        {"weight_title": -1},
        {"weight_title": 0, "weight_artist": 0},
        {"duration_tolerance_s": 20},
        {"flag_penalties": {"liev": 30}},
    ],
)
def test_invalid_config_is_refused(bad):
    with pytest.raises(ValueError):
        MatchConfig(**bad)
