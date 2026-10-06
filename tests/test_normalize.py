import pytest

from daily_mix_sync import normalize
from daily_mix_sync.models import SourceTrack
from daily_mix_sync.normalize import normalize_text, normalize_track, source_key, split_artists


def title(raw: str) -> normalize.NormalizedTrack:
    return normalize_track(raw, "Artist")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Mr. Brightside", "mr brightside"),
        ("  HUMBLE.  ", "humble"),
        ("What's    Up?", "whats up"),
        ("Don't Stop Me Now", "dont stop me now"),
        ("Don’t Stop Me Now", "dont stop me now"),  # typographic apostrophe
        ("Déjà Vu", "deja vu"),
        ("Sigur Rós", "sigur ros"),
        ("R.E.M.", "rem"),
        ("AC/DC", "ac dc"),
        ("Rock & Roll", "rock and roll"),
        ("ＡＢＣ　１２３", "abc 123"),  # full-width forms
        ("!!!", ""),
    ],
)
def test_normalize_text(raw, expected):
    assert normalize_text(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "Song - Remastered 2011",
        "Song (Remastered 2011)",
        "Song - Remastered",
        "Song (2011 Remaster)",
        "Song [2009 Remastered Version]",
        "Song – 2015 Remaster",  # en dash
        "Song (Digitally Remastered)",
        "Song (Re-Mastered)",
    ],
)
def test_remaster_suffix_becomes_a_flag(raw):
    n = title(raw)
    assert n.title == "song"
    assert n.flags == {normalize.REMASTER}
    assert n.qualifiers == frozenset()


@pytest.mark.parametrize(
    "raw",
    [
        "Song (Deluxe Edition)",
        "Song [Super Deluxe]",
        "Song - Bonus Track",
        "Song (Album Version)",
        "Song - Single Version",
        "Song (25th Anniversary Edition)",
        "Song (Explicit)",
        "Song (Original Mix)",
    ],
)
def test_edition_suffixes_are_dropped(raw):
    n = title(raw)
    assert (n.title, n.flags, n.qualifiers) == ("song", frozenset(), frozenset())


@pytest.mark.parametrize(
    "raw",
    [
        "Song (feat. Guest)",
        "Song (Feat. Guest)",
        "Song [feat. Guest]",
        "Song (ft. Guest)",
        "Song (featuring Guest)",
        "Song (with Guest)",
        "Song feat. Guest",
        "Song featuring Guest",
        "Song - feat. Guest",
    ],
)
def test_featured_artist_moves_from_title_to_artists(raw):
    n = title(raw)
    assert n.title == "song"
    assert n.artists == ("artist", "guest")
    assert n.qualifiers == frozenset()


def test_several_featured_artists_are_split():
    n = title("Peaches (feat. Daniel Caesar & Giveon)")
    assert n.artists == ("artist", "daniel caesar", "giveon")


def test_featured_artist_already_credited_is_not_repeated():
    n = normalize_track("Song (feat. Guest)", "Artist, Guest")
    assert n.artists == ("artist", "guest")


@pytest.mark.parametrize(
    ("raw", "flag"),
    [
        ("Song (Live)", normalize.LIVE),
        ("Song - Live", normalize.LIVE),
        ("Song (Acoustic)", normalize.ACOUSTIC),
        ("Song - Acoustic Version", normalize.ACOUSTIC),
        ("Song (Remix)", normalize.REMIX),
        ("Song - Radio Edit", normalize.RADIO_EDIT),
        ("Song (Demo)", normalize.DEMO),
        ("Song [Instrumental]", normalize.INSTRUMENTAL),
    ],
)
def test_version_markers_become_flags(raw, flag):
    n = title(raw)
    assert n.title == "song"
    assert n.flags == {flag}
    assert n.qualifiers == frozenset()


@pytest.mark.parametrize(
    "raw", ["Live Forever", "Live and Let Die", "Demons", "Acoustic #3", "Remix to Ignition"]
)
def test_flag_words_in_the_title_proper_are_not_flags(raw):
    assert title(raw).flags == frozenset()


def test_version_detail_survives_next_to_its_flag():
    assert title("Song (Skrillex Remix)").qualifiers == {"skrillex"}
    live = title("Song - Live at Wembley")
    assert live.flags == {normalize.LIVE}
    assert live.qualifiers == {"at", "wembley"}


@pytest.mark.parametrize(
    ("raw", "base", "qualifiers"),
    [
        ("Love Story (Taylor's Version)", "love story", {"taylors"}),
        ("Shine On You Crazy Diamond (Pts. 1-5)", "shine on you crazy diamond", {"pts", "1", "5"}),
        ("Song - Sped Up", "song", {"sped", "up"}),
        ("(I Can't Get No) Satisfaction", "satisfaction", {"i", "cant", "get", "no"}),
    ],
)
def test_unrecognised_qualifiers_are_kept(raw, base, qualifiers):
    n = title(raw)
    assert n.title == base
    assert n.qualifiers == qualifiers
    assert n.flags == frozenset()


def test_stacked_qualifiers():
    n = title("Song (feat. Guest) [Live] - 2011 Remaster")
    assert n.title == "song"
    assert n.artists == ("artist", "guest")
    assert n.flags == {normalize.LIVE, normalize.REMASTER}
    assert n.qualifiers == frozenset()


def test_title_made_only_of_a_qualifier_keeps_its_text():
    assert title("(Nice Dream)").title == "nice dream"


def test_title_made_only_of_punctuation_keeps_its_text():
    assert title("÷").title == "÷"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Calvin Harris, Rihanna", ["calvin harris", "rihanna"]),
        ("Queen & David Bowie", ["queen", "david bowie"]),
        ("Simon and Garfunkel", ["simon", "garfunkel"]),
        ("Artist feat. Guest", ["artist", "guest"]),
        ("The Beatles", ["beatles"]),
        ("Beyoncé, JAY-Z", ["beyonce", "jay z"]),
        ("Years & Years", ["years"]),
        ("Lil Nas X", ["lil nas x"]),
        ("!!!", ["!!!"]),
    ],
)
def test_split_artists(raw, expected):
    assert split_artists(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("A Night at the Opera (Deluxe Edition) [2011 Remaster]", "a night at the opera"),
        ("This Is What You Came For (feat. Rihanna) - Single", "this is what you came for"),
        ("My Iron Lung - EP", "my iron lung"),
        ("Fearless (Taylor's Version)", "fearless taylors"),
        ("Rumours (Live)", "rumours live"),
    ],
)
def test_album_normalization(raw, expected):
    assert normalize_track("Song", "Artist", raw).album == expected


def test_album_remaster_marker_flags_the_track():
    assert normalize_track("Song", "Artist", "Album (2011 Remaster)").flags == {normalize.REMASTER}


def test_album_live_marker_does_not_flag_the_track():
    assert normalize_track("Song", "Artist", "Album (Live)").flags == frozenset()


def test_source_key_prefers_spotify_id():
    track = SourceTrack("Song", "Artist", spotify_track_id="abc123")
    assert source_key(track) == "spotify:track:abc123"


def test_source_key_from_metadata_ignores_formatting():
    a = SourceTrack("Don't Stop Me Now", "Queen", "Jazz", 209_400)
    b = SourceTrack("DON’T STOP  ME NOW", "queen", "Jazz (Deluxe Edition)", 208_900)
    assert source_key(a) == source_key(b) == "meta:queen|dont stop me now|jazz|209"


def test_source_key_from_metadata_separates_different_tracks():
    studio = SourceTrack("Song", "Artist", "Album", 200_000)
    keys = {
        source_key(studio),
        source_key(SourceTrack("Song - Live", "Artist", "Album", 200_000)),
        source_key(SourceTrack("Song", "Other Artist", "Album", 200_000)),
        source_key(SourceTrack("Song", "Artist", "Other Album", 200_000)),
        source_key(SourceTrack("Song", "Artist", "Album", 260_000)),
        source_key(SourceTrack("Song", "Artist")),
    }
    assert len(keys) == 6
