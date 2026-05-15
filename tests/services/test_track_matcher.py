"""Tests for TrackMatcher — one test per strategy."""

from __future__ import annotations

import pytest

from rekordbox_set_list_manager.models.enums import MatchStatus, TrackSource
from rekordbox_set_list_manager.models.track import Track
from rekordbox_set_list_manager.services.track_matcher import MatchStrategy, TrackMatcher


def _spotify(title: str, artist: str, isrc: str | None = None) -> Track:
    return Track(
        title=title,
        artist=artist,
        isrc=isrc,
        source=TrackSource.SPOTIFY,
        match_status=MatchStatus.UNMATCHED,
    )


def _local(
    title: str,
    artist: str,
    isrc: str | None = None,
    filepath: str | None = None,
    bpm: float | None = 128.0,
    key: str | None = "8A",
) -> Track:
    return Track(
        title=title,
        artist=artist,
        isrc=isrc,
        filepath=filepath,
        bpm=bpm,
        key=key,
        source=TrackSource.REKORDBOX,
    )


@pytest.fixture
def matcher():
    return TrackMatcher()


# ---------------------------------------------------------------------------
# ISRC strategy
# ---------------------------------------------------------------------------


def test_isrc_match(matcher):
    sp = _spotify("Hey Jude", "The Beatles", isrc="GBAYE6800012")
    local = _local("Hey Jude", "Beatles", isrc="GBAYE6800012", filepath="/music/hey_jude.mp3")

    results = matcher.match([sp], [local])

    assert len(results) == 1
    r = results[0]
    assert r.strategy == MatchStrategy.ISRC
    assert r.score == 1.0
    assert r.local_track is not None
    assert r.local_track.filepath == "/music/hey_jude.mp3"
    assert r.local_track.match_status == MatchStatus.MATCHED
    # Spotify metadata preserved
    assert r.local_track.title == "Hey Jude"
    assert r.local_track.isrc == "GBAYE6800012"


def test_isrc_not_matched_when_both_none(matcher):
    sp = _spotify("Hey Jude", "The Beatles", isrc=None)
    local = _local("Hey Jude", "The Beatles", isrc=None)

    results = matcher.match([sp], [local])
    # Falls through to EXACT match
    assert results[0].strategy == MatchStrategy.EXACT


# ---------------------------------------------------------------------------
# EXACT strategy
# ---------------------------------------------------------------------------


def test_exact_match(matcher):
    sp = _spotify("Bohemian Rhapsody", "Queen")
    local = _local("Bohemian Rhapsody", "Queen", filepath="/music/bohemian.flac")

    results = matcher.match([sp], [local])

    r = results[0]
    assert r.strategy == MatchStrategy.EXACT
    assert r.score == 1.0
    assert r.local_track is not None
    assert r.local_track.bpm == 128.0


def test_exact_match_is_case_insensitive(matcher):
    sp = _spotify("bohemian rhapsody", "queen")
    local = _local("Bohemian Rhapsody", "QUEEN")

    results = matcher.match([sp], [local])
    assert results[0].strategy == MatchStrategy.EXACT


# ---------------------------------------------------------------------------
# FUZZY strategy
# ---------------------------------------------------------------------------


def test_fuzzy_match(matcher):
    sp = _spotify("hey jude", "beatles")
    local = _local("Hey Jude", "The Beatles", filepath="/music/hey_jude.mp3")

    results = matcher.match([sp], [local])

    r = results[0]
    assert r.strategy == MatchStrategy.FUZZY
    assert r.score >= 0.85
    assert r.local_track is not None


def test_fuzzy_no_match_below_threshold(matcher):
    sp = _spotify("Smells Like Teen Spirit", "Nirvana")
    local = _local("Bohemian Rhapsody", "Queen")

    results = matcher.match([sp], [local])
    assert results[0].strategy == MatchStrategy.NONE


# ---------------------------------------------------------------------------
# FILENAME strategy
# ---------------------------------------------------------------------------


def test_filename_match_artist_dash_title(matcher):
    sp = _spotify("Hey Jude", "The Beatles")
    local = _local(
        "Unknown",
        "Unknown",
        filepath="/music/The Beatles - Hey Jude.mp3",
        bpm=75.0,
        key="5A",
    )

    results = matcher.match([sp], [local])

    r = results[0]
    assert r.strategy == MatchStrategy.FILENAME
    assert r.local_track is not None
    assert r.local_track.bpm == 75.0


def test_filename_match_title_dash_artist(matcher):
    sp = _spotify("Hey Jude", "Beatles")
    local = _local(
        "Unknown",
        "Unknown",
        filepath="/music/Hey Jude - Beatles.flac",
    )

    results = matcher.match([sp], [local])
    assert results[0].strategy == MatchStrategy.FILENAME


def test_filename_no_match_when_filepath_none(matcher):
    sp = _spotify("Song", "Artist")
    local = _local("Unknown", "Unknown", filepath=None)

    results = matcher.match([sp], [local])
    assert results[0].strategy == MatchStrategy.NONE


# ---------------------------------------------------------------------------
# NONE strategy
# ---------------------------------------------------------------------------


def test_no_match(matcher):
    sp = _spotify("Completely Different Song", "Random Artist")
    local = _local("Bohemian Rhapsody", "Queen", filepath="/music/bohemian.mp3")

    results = matcher.match([sp], [local])
    r = results[0]
    assert r.strategy == MatchStrategy.NONE
    assert r.local_track is None
    assert r.score == 0.0


def test_empty_collection(matcher):
    sp = _spotify("Hey Jude", "The Beatles")

    results = matcher.match([sp], [])

    assert len(results) == 1
    assert results[0].strategy == MatchStrategy.NONE


def test_multiple_spotify_tracks(matcher):
    sp1 = _spotify("Hey Jude", "The Beatles", isrc="GBAYE6800012")
    sp2 = _spotify("Bohemian Rhapsody", "Queen")
    local1 = _local("Hey Jude", "The Beatles", isrc="GBAYE6800012", filepath="/hey.mp3")
    local2 = _local("Bohemian Rhapsody", "Queen", filepath="/boh.mp3")

    results = matcher.match([sp1, sp2], [local1, local2])

    assert results[0].strategy == MatchStrategy.ISRC
    assert results[1].strategy == MatchStrategy.EXACT


# ---------------------------------------------------------------------------
# merge: local metadata copied onto result
# ---------------------------------------------------------------------------


def test_merge_copies_local_metadata(matcher):
    sp = _spotify("Song", "Artist", isrc="XY12345")
    local = _local(
        "Song",
        "Artist",
        isrc="XY12345",
        filepath="/local/song.mp3",
        bpm=130.5,
        key="2B",
    )

    results = matcher.match([sp], [local])
    merged = results[0].local_track
    assert merged is not None
    assert merged.filepath == "/local/song.mp3"
    assert merged.bpm == 130.5
    assert merged.key == "2B"
    # Spotify fields preserved
    assert merged.spotify_id == sp.spotify_id
    assert merged.source == TrackSource.SPOTIFY
