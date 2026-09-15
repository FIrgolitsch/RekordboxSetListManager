"""Tests for TrackMatcher — one test per strategy plus index/duplicate behavior."""

from __future__ import annotations

import pytest

from rekordbox_set_list_manager.models.enums import MatchStatus, TrackSource
from rekordbox_set_list_manager.models.track import Track
from rekordbox_set_list_manager.services import track_matcher
from rekordbox_set_list_manager.services.skipped_item import SkippedItem
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
    rekordbox_id: int | None = None,
) -> Track:
    return Track(
        title=title,
        artist=artist,
        isrc=isrc,
        filepath=filepath,
        bpm=bpm,
        key=key,
        source=TrackSource.REKORDBOX,
        rekordbox_id=rekordbox_id,
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
# ISRC duplicates — deterministic resolution + report channel
# ---------------------------------------------------------------------------


def test_isrc_duplicate_resolves_to_lowest_rekordbox_id(matcher):
    sp = _spotify("Song", "Artist", isrc="DUPE1")
    high = _local("Song", "Artist", isrc="DUPE1", rekordbox_id=99)
    low = _local("Song", "Artist", isrc="DUPE1", rekordbox_id=4)

    results = matcher.match([sp], [high, low])

    assert results[0].strategy == MatchStrategy.ISRC
    assert results[0].local_track is not None
    assert results[0].local_track.rekordbox_id == 4


def test_isrc_duplicate_resolution_is_order_independent(matcher):
    sp = _spotify("Song", "Artist", isrc="DUPE1")
    low = _local("Song", "Artist", isrc="DUPE1", rekordbox_id=4)
    high = _local("Song", "Artist", isrc="DUPE1", rekordbox_id=99)

    results = matcher.match([sp], [low, high])

    assert results[0].local_track is not None
    assert results[0].local_track.rekordbox_id == 4


def test_isrc_duplicates_reported_on_channel(matcher):
    sp = _spotify("Song", "Artist", isrc="DUPE1")
    locals_ = [
        _local("Song", "Artist", isrc="DUPE1", rekordbox_id=9),
        _local("Song", "Artist", isrc="DUPE1", rekordbox_id=4),
        _local("Song", "Artist", isrc="DUPE1", rekordbox_id=7),
        _local("Other", "Other", isrc="UNIQ1", rekordbox_id=1),
    ]

    matcher.match([sp], locals_)

    assert matcher.isrc_duplicates == [
        SkippedItem(
            label="DUPE1",
            reason="3 tracks share this ISRC; lowest Rekordbox ID used",
        )
    ]


def test_isrc_duplicates_reset_per_match_call(matcher):
    sp = _spotify("Song", "Artist", isrc="DUPE1")
    dupes = [
        _local("Song", "Artist", isrc="DUPE1", rekordbox_id=9),
        _local("Song", "Artist", isrc="DUPE1", rekordbox_id=4),
    ]
    matcher.match([sp], dupes)
    assert len(matcher.isrc_duplicates) == 1

    clean = [_local("Song", "Artist", isrc="UNIQ1", rekordbox_id=1)]
    matcher.match([sp], clean)
    assert matcher.isrc_duplicates == []


def test_isrc_duplicates_empty_when_no_duplicates(matcher):
    sp = _spotify("Song", "Artist", isrc="UNIQ1")
    local = _local("Song", "Artist", isrc="UNIQ1", rekordbox_id=1)

    matcher.match([sp], [local])

    assert matcher.isrc_duplicates == []


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


def test_exact_match_duplicate_resolves_to_lowest_rekordbox_id(matcher):
    sp = _spotify("Song", "Artist")
    high = _local("Song", "Artist", rekordbox_id=20)
    low = _local("song", "artist", rekordbox_id=3)

    results = matcher.match([sp], [high, low])

    assert results[0].strategy == MatchStrategy.EXACT
    assert results[0].local_track is not None
    assert results[0].local_track.rekordbox_id == 3


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


def test_fuzzy_processor_regression_pair(matcher):
    """Mixed-case/punctuation pair only reaches threshold with default_process.

    Without ``processor=utils.default_process`` rapidfuzz scores this pair at
    ~51; with the processor it scores 100. Regression guard for the threshold
    calibration.
    """
    sp = _spotify("Song (Original Mix)!!!", "DJ Koze")
    local = _local("song original mix", "DJ Koze", filepath="/music/song.mp3")

    results = matcher.match([sp], [local])

    r = results[0]
    assert r.strategy == MatchStrategy.FUZZY
    assert r.score >= 0.85
    assert r.local_track is not None
    assert r.local_track.filepath == "/music/song.mp3"


def test_fuzzy_score_scaled_to_zero_one(matcher):
    sp = _spotify("Song (Original Mix)!!!", "DJ Koze")
    local = _local("song original mix", "DJ Koze")

    results = matcher.match([sp], [local])

    assert 0.0 <= results[0].score <= 1.0


def test_fuzzy_threshold_boundary_85_matches(matcher, monkeypatch):
    monkeypatch.setattr(track_matcher.fuzz, "token_sort_ratio", lambda *args, **kwargs: 85.0)
    sp = _spotify("Abc", "Xyz")
    local = _local("Different Title", "Xyz")

    results = matcher.match([sp], [local])

    assert results[0].strategy == MatchStrategy.FUZZY
    assert results[0].score == pytest.approx(0.85)


def test_fuzzy_threshold_boundary_849_does_not_match(matcher, monkeypatch):
    monkeypatch.setattr(track_matcher.fuzz, "token_sort_ratio", lambda *args, **kwargs: 84.9)
    sp = _spotify("Abc", "Xyz")
    local = _local("Different Title", "Xyz")

    results = matcher.match([sp], [local])

    assert results[0].strategy == MatchStrategy.NONE


def test_fuzzy_tie_resolves_to_lowest_rekordbox_id(matcher, monkeypatch):
    monkeypatch.setattr(track_matcher.fuzz, "token_sort_ratio", lambda *args, **kwargs: 90.0)
    sp = _spotify("Whatever", "Artist")
    high = _local("Alpha", "Artist", rekordbox_id=99)
    low = _local("Beta", "Artist", rekordbox_id=3)

    results = matcher.match([sp], [high, low])

    assert results[0].strategy == MatchStrategy.FUZZY
    assert results[0].local_track is not None
    assert results[0].local_track.rekordbox_id == 3


def test_fuzzy_identical_choice_strings_dedupe_to_lowest_id(matcher):
    sp = _spotify("Same Song!!", "Artist")
    high = _local("same song", "Artist", rekordbox_id=50)
    low = _local("same song", "Artist", rekordbox_id=2)

    results = matcher.match([sp], [high, low])

    assert results[0].strategy == MatchStrategy.FUZZY
    assert results[0].local_track is not None
    assert results[0].local_track.rekordbox_id == 2


def test_fuzzy_prefilter_avoids_full_collection_scan(matcher, monkeypatch):
    """The fuzzy stage scores only the pre-filtered bucket, not the collection."""
    calls: list[list[str]] = []
    real_extract = track_matcher.process.extract

    def spy(query, choices, **kwargs):
        calls.append(list(choices))
        return real_extract(query, choices, **kwargs)

    monkeypatch.setattr(track_matcher.process, "extract", spy)

    locals_ = [_local(f"Zed Track {i}", "Zed Artist") for i in range(30)]
    locals_.append(_local("song name", "Real Artist"))
    sp = _spotify("Song Name!", "Real Artist")

    results = matcher.match([sp], locals_)

    assert results[0].strategy == MatchStrategy.FUZZY
    assert len(calls) == 1
    # Only the same-initial bucket was scored, not all 31 collection tracks
    assert len(calls[0]) == 1


def test_fuzzy_passes_default_process_and_cutoff(matcher, monkeypatch):
    captured: list[dict] = []
    real_extract = track_matcher.process.extract

    def spy(query, choices, **kwargs):
        captured.append(kwargs)
        return real_extract(query, choices, **kwargs)

    monkeypatch.setattr(track_matcher.process, "extract", spy)

    sp = _spotify("Song Name!", "Real Artist")
    local = _local("song name", "Real Artist")
    matcher.match([sp], [local])

    assert len(captured) == 1
    assert captured[0]["processor"] is track_matcher.utils.default_process
    assert captured[0]["scorer"] is track_matcher.fuzz.token_sort_ratio
    assert captured[0]["score_cutoff"] == TrackMatcher.FUZZY_THRESHOLD


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


def test_filename_tie_resolves_to_lowest_rekordbox_id(matcher):
    sp = _spotify("Hey Jude", "The Beatles")
    high = _local(
        "Unknown",
        "Unknown",
        filepath="/music/The Beatles - Hey Jude.mp3",
        rekordbox_id=88,
    )
    low = _local(
        "Unknown",
        "Unknown",
        filepath="/other/The Beatles - Hey Jude.mp3",
        rekordbox_id=11,
    )

    results = matcher.match([sp], [high, low])

    assert results[0].strategy == MatchStrategy.FILENAME
    assert results[0].local_track is not None
    assert results[0].local_track.rekordbox_id == 11


# ---------------------------------------------------------------------------
# NONE strategy / edge cases
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


def test_empty_title_no_match_no_raise(matcher):
    sp = _spotify("", "The Beatles")
    local = _local("Hey Jude", "The Beatles", isrc="X1", filepath="/a.mp3")

    results = matcher.match([sp], [local])

    assert results[0].strategy == MatchStrategy.NONE


def test_empty_artist_no_match_no_raise(matcher):
    sp = _spotify("Hey Jude", "")
    local = _local("Hey Jude", "The Beatles")

    results = matcher.match([sp], [local])

    assert results[0].strategy == MatchStrategy.NONE


def test_none_title_no_match_no_raise(matcher):
    sp = Track.model_construct(
        title=None,
        artist="The Beatles",
        source=TrackSource.SPOTIFY,
        match_status=MatchStatus.UNMATCHED,
    )
    local = _local("Hey Jude", "The Beatles")

    results = matcher.match([sp], [local])

    assert results[0].strategy == MatchStrategy.NONE


def test_multiple_spotify_tracks(matcher):
    sp1 = _spotify("Hey Jude", "The Beatles", isrc="GBAYE6800012")
    sp2 = _spotify("Bohemian Rhapsody", "Queen")
    local1 = _local("Hey Jude", "The Beatles", isrc="GBAYE6800012", filepath="/hey.mp3")
    local2 = _local("Bohemian Rhapsody", "Queen", filepath="/boh.mp3")

    results = matcher.match([sp1, sp2], [local1, local2])

    assert results[0].strategy == MatchStrategy.ISRC
    assert results[1].strategy == MatchStrategy.EXACT


def test_results_preserve_input_order(matcher):
    sp1 = _spotify("No Match Here", "Nobody")
    sp2 = _spotify("Hey Jude", "The Beatles")
    local = _local("Hey Jude", "The Beatles")

    results = matcher.match([sp1, sp2], [local])

    assert results[0].spotify_track is sp1
    assert results[0].strategy == MatchStrategy.NONE
    assert results[1].spotify_track is sp2
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
