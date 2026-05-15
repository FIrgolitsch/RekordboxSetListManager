"""Shared test fixtures."""

import pytest

from rekordbox_set_list_manager.models import (
    MatchStatus,
    Project,
    RekordboxColor,
    Section,
    SectionType,
    Track,
    TrackSource,
)


@pytest.fixture
def track() -> Track:
    return Track(title="Sundown", artist="DJ Koze", bpm=128.0, key="Am", duration=390)


@pytest.fixture
def track_full() -> Track:
    return Track(
        title="Pick Up",
        artist="DJ Koze",
        bpm=125.5,
        key="Fm",
        duration=420,
        isrc="DEBL41500001",
        source=TrackSource.SPOTIFY,
        spotify_id="spotify123",
        match_status=MatchStatus.MATCHED,
        color=RekordboxColor.BLUE,
    )


@pytest.fixture
def section(track: Track) -> Section:
    sec = Section(name="Peak", section_type=SectionType.PEAK, color=RekordboxColor.RED)
    sec.add_track(track.id)
    return sec


@pytest.fixture
def project(track: Track, section: Section) -> Project:
    proj = Project(name="Summer Tour")
    proj.add_track(track)
    proj.add_section(section)
    return proj
