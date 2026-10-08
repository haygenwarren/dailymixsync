"""Read-only tests of library search against the real Music app on this Mac.

Skipped unless pytest is run with --music-app. Unlike the other live tests, nothing
here changes anything: no playlist is created, emptied or filled. They search the
library and score what comes back, and that is all.

They need Automation permission for whatever app runs pytest; see the README.
"""

import pytest

from daily_mix_sync.config import Settings
from daily_mix_sync.matcher import MatchStatus
from daily_mix_sync.models import SourceTrack
from daily_mix_sync.normalize import normalize_text
from daily_mix_sync.sync import match_one, title_term

pytestmark = pytest.mark.music_app


@pytest.fixture(scope="module", autouse=True)
def nothing_changes(music):
    """The library and every playlist are the same after these tests as before."""

    def snapshot():
        return (
            music.library_size(),
            {(p.persistent_id, p.name, p.track_count) for p in music.playlists()},
        )

    before = snapshot()
    yield
    assert snapshot() == before


def test_a_titles_only_search_for_nonsense_finds_nothing(music):
    assert music.search_songs("zzqqxx notarealsongtitle", 5, titles_only=True) == []


def test_a_titles_only_search_returns_only_songs_with_the_words_in_their_title(music, songs):
    for wanted in songs:
        words = title_term(SourceTrack(wanted.title, wanted.artist)).split()
        found = music.search_songs(" ".join(words), 25, titles_only=True)
        assert found, f"{wanted.title!r} was not found by its own title"
        for song in found:
            title_words = normalize_text(song.title).split()
            for word in words:
                if word == "and":
                    continue  # dropped from every search; the library may say "&"
                assert any(w.startswith(word) for w in title_words), (word, song.title)


def test_a_titles_only_search_leaves_out_what_only_the_album_or_artist_matches(music, songs):
    # Searching everywhere for an artist's name finds their songs; searching titles
    # for it finds only songs that have those words in the title.
    wanted = songs[0]
    artist_words = normalize_text(wanted.artist).split()
    everywhere = music.search_songs(wanted.artist, 60)
    in_titles = music.search_songs(wanted.artist, 60, titles_only=True)
    assert wanted.persistent_id in {song.persistent_id for song in everywhere} or len(everywhere) == 60
    for song in in_titles:
        title_words = normalize_text(song.title).split()
        assert all(any(w.startswith(word) for w in title_words) for word in artist_words if word != "and")


def test_a_search_never_returns_more_than_it_was_asked_for(music):
    for limit in (1, 3, 60):
        assert len(music.search_songs("the", limit)) <= limit
        assert len(music.search_songs("the", limit, titles_only=True)) <= limit


def test_broad_searches_do_not_fail_on_entries_music_cannot_describe(music):
    # A library can hold an entry that the search returns but that cannot be read.
    # Whether this one does or not, a broad search has to come back with songs.
    for word in ("a", "the", "love"):
        found = music.search_songs(word, 60)
        assert all(song.persistent_id and song.title for song in found)


def test_songs_are_found_again_through_both_searches(music, songs):
    for wanted in songs:
        source = SourceTrack(wanted.title, wanted.artist, wanted.album, wanted.duration_ms)
        result = match_one(source, music, Settings())
        assert result.status is MatchStatus.MATCHED
        assert (result.chosen.title, result.chosen.artist) == (wanted.title, wanted.artist)
        # With an artist the library has never heard of, only the title search can
        # find the song, and the matcher then turns it down: the artist is wrong.
        stranger = SourceTrack(wanted.title, "Zzqqxx Notarealartist", wanted.album, wanted.duration_ms)
        result = match_one(stranger, music, Settings())
        assert wanted.persistent_id in {s.candidate.persistent_id for s in result.candidates} or len(
            result.title_search_ids
        ) == Settings().title_search_limit
        assert result.status is not MatchStatus.MATCHED
