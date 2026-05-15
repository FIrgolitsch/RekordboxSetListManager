"""Tests for models/enums.py."""

from rekordbox_set_list_manager.models.enums import (
    MatchStatus,
    RekordboxColor,
    SectionType,
    TrackSource,
)


def test_section_type_values():
    assert SectionType.PEAK == "peak"
    assert SectionType.WARM_UP == "warm_up"
    assert len(list(SectionType)) == 8


def test_section_type_is_str():
    assert isinstance(SectionType.OPENER, str)


def test_rekordbox_color_none_is_zero():
    assert RekordboxColor.NONE == 0


def test_rekordbox_color_is_int():
    assert isinstance(RekordboxColor.RED, int)


def test_rekordbox_color_values_distinct():
    non_zero = [c for c in RekordboxColor if c != RekordboxColor.NONE]
    assert len(set(non_zero)) == len(non_zero)


def test_match_status_values():
    assert MatchStatus.UNMATCHED == "unmatched"
    assert MatchStatus.MATCHED == "matched"
    assert MatchStatus.MANUALLY_MATCHED == "manually_matched"
    assert MatchStatus.CONFLICTED == "conflicted"


def test_track_source_values():
    assert TrackSource.SPOTIFY == "spotify"
    assert TrackSource.REKORDBOX == "rekordbox"
    assert len(list(TrackSource)) == 4
