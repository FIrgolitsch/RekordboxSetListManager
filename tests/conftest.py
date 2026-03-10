"""Shared test fixtures."""

import pytest

from set_manager.models import (
    MatchStatus,
    Project,
    RekordboxColor,
    Section,
    SectionType,
    SetList,
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
def set_list(section: Section) -> SetList:
    import datetime

    sl = SetList(name="Berghain 2024-06-01", date=datetime.date(2024, 6, 1), venue="Berghain")
    sl.add_section(section)
    return sl


@pytest.fixture
def project(track: Track, set_list: SetList) -> Project:
    proj = Project(name="Summer Tour")
    proj.add_track(track)
    proj.add_set_list(set_list)
    return proj
