"""Tests for controllers.track_match.apply_track_match."""

from __future__ import annotations

from rekordbox_set_list_manager.controllers.track_match import apply_track_match
from rekordbox_set_list_manager.models.enums import MatchStatus, RekordboxColor
from rekordbox_set_list_manager.models.track import Track


def _make_track(**kwargs) -> Track:
    defaults = {
        "title": "Test",
        "artist": "Artist",
        "duration_ms": 200_000,
    }
    defaults.update(kwargs)
    return Track(**defaults)


def test_apply_none_clears_match():
    track = _make_track(
        filepath="/some/file.mp3",
        bpm=128.0,
        key="Am",
        color=RekordboxColor.RED,
        rekordbox_id=42,
        match_status=MatchStatus.MATCHED,
    )
    apply_track_match(track, None)
    assert track.filepath is None
    assert track.bpm is None
    assert track.key is None
    assert track.color is None
    assert track.rekordbox_id is None
    assert track.match_status == MatchStatus.UNMATCHED


def test_apply_local_copies_fields():
    track = _make_track()
    local = _make_track(
        filepath="/rekordbox/song.mp3",
        bpm=140.0,
        key="Cm",
        color=RekordboxColor.BLUE,
        rekordbox_id=7,
    )
    apply_track_match(track, local)
    assert track.filepath == "/rekordbox/song.mp3"
    assert track.bpm == 140.0
    assert track.key == "Cm"
    assert track.color == RekordboxColor.BLUE
    assert track.rekordbox_id == 7
    assert track.match_status == MatchStatus.MANUALLY_MATCHED


def test_apply_skips_none_fields_from_local():
    """Fields not present on local should not overwrite the destination."""
    track = _make_track(filepath="/original.mp3", bpm=130.0)
    local = _make_track()  # no filepath/bpm/color/etc.
    apply_track_match(track, local)
    assert track.filepath == "/original.mp3"
    assert track.bpm == 130.0
    assert track.match_status == MatchStatus.MANUALLY_MATCHED


def test_apply_skips_empty_filepath():
    track = _make_track()
    local = _make_track(filepath="")
    apply_track_match(track, local)
    assert track.filepath is None  # empty string is falsy — skipped
