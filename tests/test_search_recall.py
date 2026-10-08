"""Finding candidates in the library: the two-step search, and what it must not change.

These tests come out of an audit of a real Daily Mix against a real library. That
audit showed two ways a song that is in the library could go unfound, neither of them
a scoring problem:

- Music answers a search in the library's own order, not best first, so a song whose
  title is also the name of its album sits behind the rest of that album;
- a search needs every word to be present, so one artist word that the library
  spells differently hides the song.

Both are answered by looking at more candidates. Nothing here loosens what is
accepted: every candidate goes through the same matcher and the same thresholds.

The songs are made up, apart from a few public ones named in the audit as cases to
keep right. All searching goes through the real MusicApp code against FakeMusic, which
searches the way Music does: every word must be there, and results come in library
order.
"""

from __future__ import annotations

import json

import pytest

from daily_mix_sync import cli, music_app
from daily_mix_sync.config import Settings
from daily_mix_sync.database import MappingStore
from daily_mix_sync.importer import load_playlist
from daily_mix_sync.matcher import MatchConfig, MatchResult, MatchStatus, match_track
from daily_mix_sync.models import AppleCandidate, SourceTrack
from daily_mix_sync.music_app import MusicApp
from daily_mix_sync.review import REVIEW_CHOICES, review_results
from daily_mix_sync.sync import match_one, match_playlist, search_term, title_term
from fake_music import FakeMusic

ACCEPT = MatchConfig().auto_accept_threshold
REVIEW = MatchConfig().review_threshold


def library(*songs: AppleCandidate) -> tuple[MusicApp, FakeMusic]:
    fake = FakeMusic(library=songs)
    return MusicApp(run=fake), fake


def searches(fake: FakeMusic) -> list[tuple[str, str, str]]:
    """Every library search sent, as (words, limit, where)."""
    return [tuple(args) for script, args in fake.calls if script is music_app._SEARCH]


def album(name: str, artist: str, count: int, prefix: str = "AL") -> list[AppleCandidate]:
    """`count` songs of one album, none of them named after it."""
    return [
        AppleCandidate(f"{prefix}{i:03d}", f"Interlude {i}", artist, name, 180_000 + i * 1000)
        for i in range(1, count + 1)
    ]


# --- matches that already worked, and must keep working ---------------------------------


@pytest.mark.parametrize(
    ("source", "in_library"),
    [
        (
            SourceTrack("Die With A Smile", "Lady Gaga, Bruno Mars", "MAYHEM", 251_000),
            AppleCandidate("L1", "Die With A Smile", "Lady Gaga & Bruno Mars", "Die With A Smile - Single", 252_000),
        ),
        (
            SourceTrack("Rewrite The Stars", "Zac Efron, Zendaya", "Rewrite The Stars", 217_000),
            AppleCandidate(
                "L2", "Rewrite the Stars", "Zac Efron & Zendaya",
                "The Greatest Showman (Original Motion Picture Soundtrack)", 217_000,
            ),
        ),
    ],
)
def test_the_same_recording_on_a_different_album_is_matched(source, in_library):
    music, fake = library(in_library)
    result = match_one(source, music, Settings())
    assert result.status is MatchStatus.MATCHED
    assert result.chosen == in_library
    assert ACCEPT <= result.best.score < 100  # the album difference costs something, not the match
    assert len(searches(fake)) == 1, "the first search settled it; no broader search was made"


def test_capitals_and_a_few_seconds_do_not_matter():
    source = SourceTrack("Too Good At Goodbyes", "Sam Smith", "The Thrill Of It All (Special Edition)", 201_000)
    for seconds in (198, 201, 204):
        in_library = AppleCandidate(
            "L1", "Too Good at Goodbyes", "Sam Smith", "The Thrill of It All (Special Edition)", seconds * 1000
        )
        music, _ = library(in_library)
        result = match_one(source, music, Settings())
        assert result.status is MatchStatus.MATCHED
        assert result.best.score == 100


# --- the first search looks deep enough ---------------------------------------------------


def test_a_title_track_behind_its_own_album_is_found():
    # Searching "night drive juno vale" brings back every song on the album Night
    # Drive, in library order. The song Night Drive is the 31st of them.
    title_track = AppleCandidate("NIGHT", "Night Drive", "Juno Vale", "Night Drive", 214_000)
    music, fake = library(*album("Night Drive", "Juno Vale", 30), title_track)
    source = SourceTrack("Night Drive", "Juno Vale", "Night Drive", 214_000)

    result = match_one(source, music, Settings())
    assert result.status is MatchStatus.MATCHED
    assert result.chosen == title_track
    assert len(searches(fake)) == 1

    # Looking at the first ten results only, with no second search, is how it used
    # to be done, and it missed this song.
    shallow = match_one(source, music, Settings(search_limit=10, title_search_limit=0))
    assert shallow.status is MatchStatus.FAILED
    assert title_track not in [scored.candidate for scored in shallow.candidates]


def test_the_best_version_wins_wherever_it_sits_in_the_library():
    # An acceptable remaster comes early; the exact recording is the 40th result.
    remaster = AppleCandidate("REMASTER", "Night Drive (Remastered)", "Juno Vale", "Night Drive", 214_000)
    exact = AppleCandidate("EXACT", "Night Drive", "Juno Vale", "Night Drive", 214_000)
    music, _ = library(remaster, *album("Night Drive", "Juno Vale", 38), exact)
    result = match_one(SourceTrack("Night Drive", "Juno Vale", "Night Drive", 214_000), music, Settings())
    assert result.chosen == exact
    assert [scored.candidate for scored in result.candidates[:2]] == [exact, remaster]


# --- the second search: title alone ---------------------------------------------------------


def test_a_title_track_too_deep_for_the_first_search_is_found_by_title():
    title_track = AppleCandidate("NIGHT", "Night Drive", "Juno Vale", "Night Drive", 214_000)
    music, fake = library(*album("Night Drive", "Juno Vale", 75), title_track)
    result = match_one(SourceTrack("Night Drive", "Juno Vale", "Night Drive", 214_000), music, Settings())
    assert result.status is MatchStatus.MATCHED
    assert result.chosen == title_track
    assert searches(fake) == [("night drive juno vale", "60", "all"), ("night drive", "25", "names")]
    assert result.title_search == "night drive"
    assert result.title_search_ids == {"NIGHT"}


def test_a_song_whose_other_artist_is_not_credited_in_the_library_is_found_by_title():
    # Spotify lists two artists and puts the one the library does not mention first.
    in_library = AppleCandidate("L1", "Night Signals", "Mara Lindqvist", "Night Signals - Single", 201_500)
    music, fake = library(in_library)
    source = SourceTrack("Night Signals", "DJ Kestrel, Mara Lindqvist", "Night Signals", 201_000)

    result = match_one(source, music, Settings())
    assert searches(fake)[0] == ("night signals dj kestrel", "60", "all")
    assert searches(fake)[1] == ("night signals", "25", "names")
    assert result.status is MatchStatus.MATCHED
    assert result.chosen == in_library
    assert result.best.artist_score < 100, "the missing artist still counts against it"


@pytest.mark.parametrize(
    ("spotify_artist", "library_artist"),
    [
        ("The Night Owls", "Night Owls"),
        ("Night Owls", "The Night Owls"),
        ("Juno Vale", "Juno Vale & The Night Owls"),  # solo on Spotify, with the band in the library
        ("Juno Vale and the Night Owls", "Juno Vale"),  # and the other way round
        ("Juno Vale, Mara Lindqvist", "Juno Vale & Mara Lindqvist"),
        ("Mara Lindqvist, Juno Vale", "Juno Vale"),  # the library names only the second artist
        ("Juno Vale", "Juno Vale feat. Mara Lindqvist"),
    ],
)
def test_artist_credit_differences_do_not_hide_a_song(spotify_artist, library_artist):
    in_library = AppleCandidate("L1", "Harbor Lights", library_artist, "Harbor Lights", 215_000)
    music, _ = library(in_library)
    result = match_one(SourceTrack("Harbor Lights", spotify_artist, "Harbor Lights", 215_000), music, Settings())
    assert in_library in [scored.candidate for scored in result.candidates], "the song is found"
    # Whether it is accepted is the matcher's decision, exactly as if it had been
    # found by the first search.
    assert result.status is match_track(result.track, [in_library]).status
    assert result.best.score == match_track(result.track, [in_library]).best.score


def test_the_title_search_is_not_made_when_the_first_search_gives_a_match():
    music, fake = library(AppleCandidate("L1", "Paper Planes", "Ines Moreau", "Atlas", 198_000))
    result = match_one(SourceTrack("Paper Planes", "Ines Moreau", "Atlas", 198_000), music, Settings())
    assert result.status is MatchStatus.MATCHED
    assert searches(fake) == [("paper planes ines moreau", "60", "all")]
    assert result.title_search == ""
    assert result.title_search_ids == frozenset()


def test_the_title_search_can_be_turned_off():
    music, fake = library(AppleCandidate("L1", "Night Signals", "Mara Lindqvist", "", 201_500))
    source = SourceTrack("Night Signals", "DJ Kestrel, Mara Lindqvist", "", 201_000)
    result = match_one(source, music, Settings(title_search_limit=0))
    assert result.status is MatchStatus.FAILED
    assert len(searches(fake)) == 1


def test_a_title_made_of_punctuation_is_searched_like_any_other_and_harms_nothing():
    # Music takes such a search without complaint; it finds little or nothing.
    music, fake = library(AppleCandidate("L1", "Paper Planes", "Ines Moreau", "Atlas", 198_000))
    source = SourceTrack("???", "Ines Moreau")
    assert title_term(source) == "???"
    result = match_one(source, music, Settings())
    assert result.status is MatchStatus.FAILED
    assert searches(fake) == [(search_term(source), "60", "all"), ("???", "25", "names")]


def test_the_title_search_uses_the_base_title_without_version_words():
    assert title_term(SourceTrack("Paper Planes - Remastered 2011", "Ines Moreau")) == "paper planes"
    assert title_term(SourceTrack("Paper Planes (feat. Juno Vale)", "Ines Moreau")) == "paper planes"
    assert title_term(SourceTrack("You Are The Reason - Duet Version", "Calum Scott, Leona Lewis")) == "you are the reason"


# --- a wider search is not a looser match ---------------------------------------------------


DUET = SourceTrack(
    "You Are The Reason - Duet Version", "Calum Scott, Leona Lewis", "Only Human (Special Edition)", 190_000
)
SOLO = AppleCandidate("SOLO", "You Are the Reason", "Calum Scott", "Only Human (Deluxe)", 204_000)
INSTRUMENTAL = AppleCandidate(
    "INST", "You Are The Reason (Instrumental)", "Calum Scott", "You Are The Reason (Instrumental) - Single", 204_000
)


def test_a_duet_is_not_matched_to_the_solo_recording():
    music, fake = library(SOLO, INSTRUMENTAL)
    result = match_one(DUET, music, Settings())
    assert result.status is MatchStatus.FAILED, "not matched, and not offered for review either"
    assert result.chosen is None
    assert result.best.candidate == SOLO
    assert result.best.score < REVIEW
    assert ("qualifier mismatch: duet", 20.0) in result.best.penalties
    assert ("you are the reason", "25", "names") in searches(fake), "the broader search was made"
    assert len(result.candidates) == 2, "and it changed nothing: the same two songs, each scored once"


def test_a_duet_left_out_is_not_remembered(tmp_path):
    export = tmp_path / "mix.json"
    export.write_text(json.dumps({"playlist_name": "Daily Mix 4", "tracks": [{
        "title": DUET.title, "artist": DUET.artist, "album": DUET.album, "duration_ms": DUET.duration_ms,
        "spotify_track_id": "duet0000000000000000001",
    }]}), encoding="utf-8")
    music, _ = library(SOLO, INSTRUMENTAL)
    with MappingStore(tmp_path / "mappings.sqlite3") as store:
        results = match_playlist(load_playlist(export), music, store, Settings())
        assert [r.status for r in results] == [MatchStatus.FAILED]
        assert store.count() == 0


def test_the_duet_is_matched_when_the_library_has_the_duet():
    duet = AppleCandidate(
        "DUET", "You Are the Reason (Duet Version)", "Calum Scott & Leona Lewis", "Only Human (Special Edition)", 190_000
    )
    music, _ = library(SOLO, INSTRUMENTAL, duet)
    result = match_one(DUET, music, Settings())
    assert result.status is MatchStatus.MATCHED
    assert result.chosen == duet


@pytest.mark.parametrize(
    ("wanted", "only_version_in_library", "why"),
    [
        ("Paper Planes", "Paper Planes (Live at the Olympia)", "live"),
        ("Paper Planes - Live at the Olympia", "Paper Planes", "live"),
        ("Paper Planes", "Paper Planes (Kestrel Remix)", "remix"),
        ("Paper Planes", "Paper Planes (Acoustic)", "acoustic"),
        ("Paper Planes", "Paper Planes (Demo)", "demo"),
        ("Paper Planes", "Paper Planes (Instrumental)", "instrumental"),
        ("Paper Planes - Radio Edit", "Paper Planes", "radio_edit"),
    ],
)
def test_a_different_version_found_by_the_title_search_is_still_rejected(wanted, only_version_in_library, why):
    # The library credits the band, so only the title search can find the song at all.
    in_library = AppleCandidate("L1", only_version_in_library, "Ines Moreau Trio", "Atlas", 198_000)
    music, _ = library(in_library)
    result = match_one(SourceTrack(wanted, "The Moreau Sisters, Ines Moreau", "Atlas", 198_000), music, Settings())
    assert result.title_search_ids == {"L1"}, "found by the title search"
    assert result.status is not MatchStatus.MATCHED
    assert result.chosen is None
    assert any(reason == f"version mismatch: {why}" for reason, _ in result.best.penalties)


def test_the_same_version_found_by_the_title_search_is_accepted_like_any_other():
    in_library = AppleCandidate("L1", "Paper Planes (Live at the Olympia)", "Ines Moreau", "Atlas", 198_000)
    music, _ = library(in_library)
    source = SourceTrack("Paper Planes - Live at the Olympia", "The Moreau Sisters, Ines Moreau", "Atlas", 198_000)
    result = match_one(source, music, Settings())
    assert result.title_search_ids == {"L1"}
    assert result.status is MatchStatus.MATCHED


def test_a_recording_of_a_very_different_length_is_not_accepted():
    in_library = AppleCandidate("L1", "Paper Planes", "Ines Moreau Trio", "Atlas", 320_000)
    music, _ = library(in_library)
    result = match_one(SourceTrack("Paper Planes", "The Moreau Sisters, Ines Moreau", "Atlas", 198_000), music, Settings())
    assert result.title_search_ids == {"L1"}
    assert result.status is not MatchStatus.MATCHED
    assert any(reason.startswith("duration differs by") for reason, _ in result.best.penalties)


def test_among_several_songs_of_the_same_title_the_right_one_is_chosen():
    right = AppleCandidate("RIGHT", "Paper Planes", "Ines Moreau", "Atlas", 198_500)
    others = [
        AppleCandidate("O1", "Paper Planes", "Cold Harbour", "Driftwood", 199_000),
        AppleCandidate("O2", "Paper Planes (Live)", "Ines Moreau", "Live in Lyon", 240_000),
        AppleCandidate("O3", "Paper Planes", "The Lantern Parade", "Paper Planes - EP", 197_000),
        AppleCandidate("O4", "Paper Planes and Other Stories", "Marlowe", "Stories", 200_000),
    ]
    music, _ = library(*others[:2], right, *others[2:])
    source = SourceTrack("Paper Planes", "The Moreau Sisters, Ines Moreau", "Atlas", 198_000)
    result = match_one(source, music, Settings())
    assert len(result.candidates) == 5, "the title search brought back all five"
    assert result.status is MatchStatus.MATCHED
    assert result.chosen == right
    assert all(scored.score < REVIEW for scored in result.candidates[1:]), "none of the others comes close"


def test_songs_of_the_same_title_by_other_artists_are_not_accepted():
    music, fake = library(
        AppleCandidate("O1", "Be Alright", "Cold Harbour", "Driftwood", 179_000),
        AppleCandidate("O2", "Be Alright (Live)", "Cold Harbour", "Live in Lyon", 185_000),
        AppleCandidate("O3", "Be Alright", "The Lantern Parade", "Be Alright - EP", 196_000),
    )
    result = match_one(SourceTrack("Be Alright", "Ines Moreau", "Atlas", 196_000), music, Settings())
    assert searches(fake) == [("be alright ines moreau", "60", "all"), ("be alright", "25", "names")]
    assert len(result.candidates) == 3
    assert result.status is MatchStatus.FAILED
    assert result.chosen is None
    assert result.best.score < REVIEW, "a same-titled song by someone else is not even offered for review"


def test_a_song_that_is_not_in_the_library_is_still_not_in_the_library():
    music, fake = library(AppleCandidate("L1", "Paper Planes", "Ines Moreau", "Atlas", 198_000))
    result = match_one(SourceTrack("Harbor Lights", "Juno Vale", "Harbor Lights", 215_000), music, Settings())
    assert len(searches(fake)) == 2
    assert result.status is MatchStatus.FAILED
    assert result.candidates == ()
    assert result.title_search == "harbor lights"


# --- merging the two searches -----------------------------------------------------------------


def test_a_song_found_by_both_searches_is_scored_once():
    # A different version, so the first search does not settle it and the second runs.
    live = AppleCandidate("LIVE", "Paper Planes (Live)", "Ines Moreau", "Live in Lyon", 240_000)
    music, fake = library(live)
    result = match_one(SourceTrack("Paper Planes", "Ines Moreau", "Atlas", 198_000), music, Settings())
    assert len(searches(fake)) == 2, "both searches ran, and both returned the same song"
    assert [scored.candidate for scored in result.candidates] == [live]
    assert result.title_search_ids == frozenset(), "it is not counted as found by the title search"


# Spotify credits "Night Owls" first; the library credits Juno Vale alone. The first
# search asks for the words "night owls", which only one of these albums supplies.
CREDITED_TO_BOTH = SourceTrack("Harbor Lights", "Night Owls, Juno Vale", "", 215_000)


def test_on_equal_scores_the_first_search_comes_first():
    # Two copies of one recording that score the same. The library lists TITLE_ONLY
    # before FIRST, and only FIRST can be reached by the first search.
    title_only = AppleCandidate("TITLE_ONLY", "Harbor Lights (Sped Up)", "Juno Vale", "Other Sessions", 215_000)
    first = AppleCandidate("FIRST", "Harbor Lights (Sped Up)", "Juno Vale", "Night Owls Sessions", 215_000)
    music, fake = library(title_only, first)
    result = match_one(CREDITED_TO_BOTH, music, Settings())
    assert searches(fake) == [("harbor lights night owls", "60", "all"), ("harbor lights", "25", "names")]
    assert result.status is MatchStatus.REVIEW
    scores = [scored.score for scored in result.candidates]
    assert scores[0] == scores[1]
    assert [scored.candidate.persistent_id for scored in result.candidates] == ["FIRST", "TITLE_ONLY"]
    assert result.title_search_ids == {"TITLE_ONLY"}


def test_a_better_score_from_the_title_search_still_wins():
    weaker = AppleCandidate("WEAK", "Harbor Lights (Sped Up)", "Juno Vale", "Night Owls Sessions", 215_000)
    exact = AppleCandidate("EXACT", "Harbor Lights", "Juno Vale", "Other Sessions", 215_000)
    music, _ = library(weaker, exact)
    result = match_one(CREDITED_TO_BOTH, music, Settings())
    assert result.status is MatchStatus.MATCHED
    assert result.chosen == exact
    assert result.title_search_ids == {"EXACT"}
    assert [scored.candidate.persistent_id for scored in result.candidates] == ["EXACT", "WEAK"]


def test_the_same_track_is_never_listed_twice():
    songs = [AppleCandidate(f"S{i}", "Paper Planes (Live)", "Ines Moreau", f"Tour {i}", 240_000) for i in range(8)]
    music, _ = library(*songs)
    result = match_one(SourceTrack("Paper Planes", "Ines Moreau", "Atlas", 198_000), music, Settings())
    ids = [scored.candidate.persistent_id for scored in result.candidates]
    assert sorted(ids) == sorted(song.persistent_id for song in songs)
    assert len(set(ids)) == len(ids)


# --- both searches are bounded ------------------------------------------------------------------


def test_a_common_title_brings_back_no_more_than_the_limits_allow():
    same_title = [AppleCandidate(f"T{i:03d}", "Home", f"Artist {i}", f"Album {i}", 200_000) for i in range(300)]
    music, fake = library(*same_title)
    result = match_one(SourceTrack("Home", "Ines Moreau", "Atlas", 198_000), music, Settings())
    assert searches(fake) == [("home ines moreau", "60", "all"), ("home", "25", "names")]
    assert len(result.candidates) == 25, "the 300 songs called Home were not all read"
    assert result.status is MatchStatus.FAILED

    result = match_one(SourceTrack("Home", "Ines Moreau"), music, Settings(search_limit=5, title_search_limit=3))
    assert len(result.candidates) == 3


def test_the_two_limits_add_up_to_the_most_that_is_ever_scored():
    many = album("Home", "Ines Moreau", 100) + [
        AppleCandidate(f"T{i:03d}", "Home (Live)", f"Artist {i}", f"Album {i}", 200_000) for i in range(100)
    ]
    music, _ = library(*many)
    result = match_one(SourceTrack("Home", "Ines Moreau", "Home", 198_000), music, Settings())
    assert len(result.candidates) == Settings().search_limit + Settings().title_search_limit == 85


def test_one_missing_song_costs_two_searches_not_a_walk_through_the_library():
    music, fake = library(*album("Atlas", "Ines Moreau", 200))
    match_one(SourceTrack("Harbor Lights", "Juno Vale"), music, Settings())
    assert [script for script, _ in fake.calls] == [music_app._SEARCH, music_app._SEARCH]


# --- the search script itself -----------------------------------------------------------------


def search_script() -> str:
    """The body of the search script: its `on run` handler, without comments."""
    body = music_app._SEARCH[music_app._SEARCH.rindex("on run argv") :]
    return "\n".join(line for line in body.splitlines() if not line.strip().startswith("--"))


def test_an_unreadable_search_result_is_skipped_not_fatal():
    # Music's search can return an entry that is not a readable track. Reading it
    # raises an error, which used to end the whole search.
    script = search_script()
    start, end = script.index("\t\t\ttry\n"), script.index("\t\t\tend try\n")
    guarded = script[start:end]
    for needed in ("properties of t", "media kind of details", "persistent ID of details", "set end of trackIDs"):
        assert needed in guarded, needed
    # Everything is read before anything is kept, so a failure part-way cannot
    # leave the five lists with different lengths.
    assert guarded.index("duration of details") < guarded.index("set end of trackIDs")
    assert "set end of" not in script[:start]


def test_each_result_is_read_with_one_request():
    script = search_script()
    assert script.count("properties of t") == 1
    for detail in ("persistent ID", "name", "artist", "album", "duration", "media kind"):
        assert f"{detail} of t\n" not in script, f"{detail} is not asked for separately"


def test_the_search_reads_and_never_changes_anything():
    script = search_script()
    for word in ("delete", "duplicate", "make", "move", "add", "set name of", "play"):
        assert word not in script.replace("playlist", ""), word
    assert script.count("search library playlist 1") == 2  # everywhere, or in song names
    assert "System Events" not in music_app._SEARCH, "no window automation"


def test_titles_only_searches_song_names_and_nothing_else():
    by_album = AppleCandidate("A1", "Interlude", "Juno Vale", "Night Drive", 100_000)
    by_title = AppleCandidate("T1", "Night Drive", "Juno Vale", "Night Drive", 214_000)
    by_artist = AppleCandidate("R1", "Something Else", "Night Drive Collective", "Other", 150_000)
    music, fake = library(by_album, by_title, by_artist)
    assert music.search_songs("night drive", 10) == [by_album, by_title, by_artist]
    assert music.search_songs("night drive", 10, titles_only=True) == [by_title]
    assert searches(fake) == [("night drive", "10", "all"), ("night drive", "10", "names")]


# --- what the person sees -------------------------------------------------------------------


@pytest.fixture
def cli_music(tmp_path, monkeypatch):
    def install(*songs):
        fake = FakeMusic(library=songs)
        monkeypatch.setattr(cli, "MusicApp", lambda prefix: MusicApp(prefix, run=fake))
        monkeypatch.setattr(cli, "_interactive", lambda: False)
        monkeypatch.chdir(tmp_path)
        return fake

    return install


def write_export(tmp_path, *tracks: SourceTrack) -> str:
    path = tmp_path / "daily_mix_4.json"
    path.write_text(json.dumps({"playlist_name": "Daily Mix 4", "tracks": [
        {"title": t.title, "artist": t.artist, "album": t.album, "duration_ms": t.duration_ms,
         "spotify_track_id": f"id{i:020d}"}
        for i, t in enumerate(tracks)
    ]}), encoding="utf-8")
    return str(path)


def test_a_dry_run_says_which_matches_the_title_search_found(cli_music, tmp_path, capsys):
    cli_music(
        AppleCandidate("L1", "Night Signals", "Mara Lindqvist", "Night Signals - Single", 201_500),
        AppleCandidate("L2", "Paper Planes", "Ines Moreau", "Atlas", 198_000),
        SOLO,
    )
    export = write_export(
        tmp_path,
        SourceTrack("Night Signals", "DJ Kestrel, Mara Lindqvist", "Night Signals", 201_000),
        SourceTrack("Paper Planes", "Ines Moreau", "Atlas", 198_000),
        DUET,
    )
    assert cli.main(["sync", export, "--dry-run", "--details"]) == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    night = next(line for line in lines if "Night Signals — Mara Lindqvist" in line)
    paper = next(line for line in lines if "new" in line and "Paper Planes — Ines Moreau" in line)
    assert night.endswith("[found by title search]")
    assert "found by title search" not in paper
    assert "New library matches:        2" in out
    assert "Not in library:             1" in out
    assert "No changes made (--dry-run)." in out


def test_music_find_shows_both_searches_and_only_the_best_candidates(cli_music, capsys):
    songs = [AppleCandidate(f"T{i:03d}", "Home", f"Artist {i}", f"Album {i}", 200_000) for i in range(40)]
    cli_music(*songs, AppleCandidate("RIGHT", "Home", "Moreau", "Atlas", 198_000))
    code = cli.main(["music-find", "Home", "Ines Moreau", "--album", "Atlas"])
    out = capsys.readouterr().out
    assert "Library search 'home ines moreau': 0 candidate(s)" in out
    assert "Title search 'home': 25 more" in out
    assert out.count("[found by title search]") == REVIEW_CHOICES
    assert f"... and {25 - REVIEW_CHOICES} more with lower scores" in out
    assert code == 1


def test_review_offers_the_best_few_candidates_not_all_of_them(tmp_path):
    source = SourceTrack("Harbor Lights", "The Night Owls", "Harbor Lights", 215_000)
    versions = [
        AppleCandidate(f"V{i:02d}", "Harbor Lights (Sped Up)", "The Night Owls", f"Harbor Lights {i}", 215_000)
        for i in range(30)
    ]
    music, _ = library(*versions)
    result = match_one(source, music, Settings())
    assert result.status is MatchStatus.REVIEW
    assert len(result.candidates) == 30

    shown: list[str] = []
    answers = iter([str(REVIEW_CHOICES + 1), str(REVIEW_CHOICES)])
    with MappingStore(tmp_path / "mappings.sqlite3") as store:
        reviewed = review_results([result], store, ask=lambda prompt: next(answers), show=shown.append)
    numbered = [line for line in shown if line.strip().split(".")[0].isdigit() and "Harbor Lights" in line]
    assert len(numbered) == REVIEW_CHOICES
    assert f"Enter a number from 1 to {REVIEW_CHOICES}, s to skip, or q to quit." in shown
    assert reviewed[0].status is MatchStatus.MANUAL
    assert reviewed[0].chosen == result.candidates[REVIEW_CHOICES - 1].candidate


def test_match_results_made_without_a_search_say_nothing_about_one():
    result = MatchResult(SourceTrack("A", "B"), MatchStatus.FAILED)
    assert result.title_search == ""
    assert result.title_search_ids == frozenset()
