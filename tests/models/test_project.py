"""Tests for models/project.py."""

import datetime
import uuid

import pytest

from rekordbox_set_list_manager.models.enums import RekordboxColor, SectionType
from rekordbox_set_list_manager.models.project import Project
from rekordbox_set_list_manager.models.section import Section
from rekordbox_set_list_manager.models.section_name_theme import SectionNameTheme
from rekordbox_set_list_manager.models.track import Track
from rekordbox_set_list_manager.utils.constants import DEFAULT_SECTION_COLORS


def test_project_defaults():
    p = Project(name="My Project")
    assert isinstance(p.id, uuid.UUID)
    assert p.sections == []
    assert p.tracks == {}
    assert p.spotify_playlist_id is None
    assert p.tidal_playlist_id is None
    assert isinstance(p.created_at, datetime.datetime)
    assert p.created_at.tzinfo is not None


def test_project_section_color_map_defaults():
    p = Project(name="P")
    assert p.section_color_map == DEFAULT_SECTION_COLORS


def test_add_and_get_track(project, track):
    assert project.get_track(track.id) is track


def test_get_track_missing(project):
    assert project.get_track(uuid.uuid4()) is None


def test_remove_track_also_removes_from_sections(project, track, section):
    assert track.id in section.track_ids
    project.remove_track(track.id)
    assert project.get_track(track.id) is None
    assert track.id not in section.track_ids


def test_remove_track_noop_if_missing(project):
    project.remove_track(uuid.uuid4())  # should not raise


# ----------------------------------------------------------------- sections


def test_add_and_get_section(project, section):
    assert project.get_section(section.id) is section


def test_get_section_missing(project):
    assert project.get_section(uuid.uuid4()) is None


def test_remove_section(project, section):
    project.remove_section(section.id)
    assert project.sections == []


def test_remove_section_noop_if_missing(project):
    project.remove_section(uuid.uuid4())  # should not raise


def test_move_section():
    p = Project(name="P")
    secs = [Section(name=f"S{i}") for i in range(3)]
    for s in secs:
        p.add_section(s)
    p.move_section(secs[2].id, 0)
    assert p.sections == [secs[2], secs[0], secs[1]]


def test_move_section_not_found():
    p = Project(name="P")
    with pytest.raises(ValueError, match="not found"):
        p.move_section(uuid.uuid4(), 0)


def test_all_track_ids(project, track):
    assert track.id in project.all_track_ids


def test_total_track_count(project):
    assert project.total_track_count == 1


def test_touch_updates_updated_at():
    p = Project(name="P")
    original = p.updated_at
    p.touch()
    assert p.updated_at >= original


def test_default_color_for():
    p = Project(name="P")
    assert p.default_color_for(SectionType.PEAK) == RekordboxColor.RED
    assert p.default_color_for(SectionType.GENERAL) == RekordboxColor.NONE


def test_default_color_for_unknown_falls_back_to_none():
    p = Project(name="P")
    del p.section_color_map[SectionType.BUILD]
    assert p.default_color_for(SectionType.BUILD) == RekordboxColor.NONE


def test_project_json_round_trip(project):
    json_str = project.model_dump_json()
    restored = Project.model_validate_json(json_str)
    assert restored.id == project.id
    assert restored.name == project.name
    assert set(restored.tracks.keys()) == set(project.tracks.keys())
    assert len(restored.sections) == len(project.sections)
    assert restored.section_color_map == project.section_color_map


def test_project_tracks_are_independent_copies():
    t1 = Track(title="A", artist="B")
    t2 = Track(title="A", artist="B")
    p = Project(name="P")
    p.add_track(t1)
    p.add_track(t2)
    assert len(p.tracks) == 2


# --------------------------------------------------------------- migration


def test_migrate_old_set_lists_format():
    """Old JSON with set_lists[0] is transparently upgraded on load."""
    old_data = {
        "id": str(uuid.uuid4()),
        "name": "Old Project",
        "set_lists": [
            {
                "id": str(uuid.uuid4()),
                "name": "Set",
                "sections": [],
                "spotify_playlist_id": "pl123",
                "tidal_playlist_id": None,
            }
        ],
        "tracks": {},
        "section_color_map": {},
        "themes": [],
    }
    proj = Project.model_validate(old_data)
    assert proj.sections == []
    assert proj.spotify_playlist_id == "pl123"


# ------------------------------------------------------------------ themes


@pytest.fixture
def dawn_theme() -> SectionNameTheme:
    return SectionNameTheme(
        name="Dawn to Dusk",
        names={SectionType.OPENER: "Dawn", SectionType.PEAK: "Dusk"},
    )


def test_project_themes_empty_by_default():
    assert Project(name="P").themes == []


def test_add_and_get_theme(project, dawn_theme):
    project.add_theme(dawn_theme)
    assert project.get_theme("Dawn to Dusk") is dawn_theme


def test_get_theme_missing(project):
    assert project.get_theme("Nonexistent") is None


def test_remove_theme(project, dawn_theme):
    project.add_theme(dawn_theme)
    project.remove_theme("Dawn to Dusk")
    assert project.get_theme("Dawn to Dusk") is None


def test_remove_theme_noop_if_missing(project):
    project.remove_theme("Ghost")  # should not raise


def test_apply_theme_renames_matching_sections(project, section, dawn_theme):
    # section has SectionType.PEAK, name currently "Peak"
    project.add_theme(dawn_theme)
    project.apply_theme("Dawn to Dusk")
    assert section.name == "Dusk"


def test_apply_theme_leaves_unmapped_sections_unchanged(project, section, dawn_theme):
    general_section = Section(name="My General", section_type=SectionType.GENERAL)
    project.add_section(general_section)

    project.add_theme(dawn_theme)
    project.apply_theme("Dawn to Dusk")

    assert general_section.name == "My General"  # untouched
    assert section.name == "Dusk"  # renamed


def test_apply_theme_missing_theme_raises(project):
    with pytest.raises(ValueError, match="Theme"):
        project.apply_theme("Nonexistent")


def test_project_json_round_trip_includes_themes(project, dawn_theme):
    project.add_theme(dawn_theme)
    json_str = project.model_dump_json()
    restored = Project.model_validate_json(json_str)
    assert len(restored.themes) == 1
    assert restored.themes[0].name == "Dawn to Dusk"
    assert restored.themes[0].names[SectionType.OPENER] == "Dawn"
