"""Tests for models/track.py."""

import uuid

import pytest

from set_manager.models.enums import MatchStatus, RekordboxColor, TrackSource
from set_manager.models.track import Track


def test_track_defaults():
    t = Track(title="Acid Rain", artist="Objekt")
    assert isinstance(t.id, uuid.UUID)
    assert t.bpm is None
    assert t.key is None
    assert t.duration is None
    assert t.isrc is None
    assert t.source == TrackSource.MANUAL
    assert t.match_status == MatchStatus.UNMATCHED
    assert t.color is None


def test_track_display_name(track):
    assert track.display_name == "DJ Koze - Sundown"


def test_track_duration_formatted(track):
    # 390 seconds = 6:30
    assert track.duration_formatted == "6:30"


def test_track_duration_formatted_none():
    t = Track(title="X", artist="Y")
    assert t.duration_formatted is None


def test_track_duration_formatted_seconds_padding():
    t = Track(title="X", artist="Y", duration=65)
    assert t.duration_formatted == "1:05"


def test_track_json_round_trip(track_full):
    json_str = track_full.model_dump_json()
    restored = Track.model_validate_json(json_str)
    assert restored == track_full
    assert restored.id == track_full.id
    assert restored.isrc == track_full.isrc
    assert restored.source == TrackSource.SPOTIFY
    assert restored.match_status == MatchStatus.MATCHED
    assert restored.color == RekordboxColor.BLUE


def test_track_ids_are_unique():
    t1 = Track(title="A", artist="B")
    t2 = Track(title="A", artist="B")
    assert t1.id != t2.id


@pytest.mark.parametrize("duration,expected", [
    (0, "0:00"),
    (59, "0:59"),
    (60, "1:00"),
    (3661, "61:01"),
])
def test_track_duration_formatted_parametrized(duration, expected):
    t = Track(title="T", artist="A", duration=duration)
    assert t.duration_formatted == expected
