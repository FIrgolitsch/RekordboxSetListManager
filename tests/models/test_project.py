"""Tests for models/project.py."""

import datetime
import uuid

import pytest

from set_manager.models.enums import RekordboxColor, SectionType
from set_manager.models.project import Project
from set_manager.models.section_name_theme import SectionNameTheme
from set_manager.models.set_list import SetList
from set_manager.models.track import Track
from set_manager.utils.constants import DEFAULT_SECTION_COLORS


def test_project_defaults():
    p = Project(name="My Project")
    assert isinstance(p.id, uuid.UUID)
    assert p.set_lists == []
    assert p.tracks == {}
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
    # The track is in the section inside the project's set_list
    assert track.id in section.track_ids
    project.remove_track(track.id)
    assert project.get_track(track.id) is None
    assert track.id not in section.track_ids


def test_remove_track_noop_if_missing(project):
    project.remove_track(uuid.uuid4())  # should not raise


def test_add_and_get_set_list(project, set_list):
    assert project.get_set_list(set_list.id) is set_list


def test_get_set_list_missing(project):
    assert project.get_set_list(uuid.uuid4()) is None


def test_remove_set_list(project, set_list):
    project.remove_set_list(set_list.id)
    assert project.set_lists == []


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
    # Remove one entry to test fallback
    del p.section_color_map[SectionType.BUILD]
    assert p.default_color_for(SectionType.BUILD) == RekordboxColor.NONE


def test_project_json_round_trip(project):
    json_str = project.model_dump_json()
    restored = Project.model_validate_json(json_str)
    assert restored.id == project.id
    assert restored.name == project.name
    assert set(restored.tracks.keys()) == set(project.tracks.keys())
    assert len(restored.set_lists) == len(project.set_lists)
    assert restored.section_color_map == project.section_color_map


def test_project_tracks_are_independent_copies():
    """Adding the same track data twice should produce two distinct entries."""
    t1 = Track(title="A", artist="B")
    t2 = Track(title="A", artist="B")
    p = Project(name="P")
    p.add_track(t1)
    p.add_track(t2)
    assert len(p.tracks) == 2


def test_project_add_multiple_set_lists():
    p = Project(name="P")
    sl1, sl2 = SetList(name="Set 1"), SetList(name="Set 2")
    p.add_set_list(sl1)
    p.add_set_list(sl2)
    assert len(p.set_lists) == 2
    assert p.get_set_list(sl1.id) is sl1
    assert p.get_set_list(sl2.id) is sl2


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


def test_apply_theme_renames_matching_sections(project, set_list, section, dawn_theme):
    # section has SectionType.PEAK, name currently "Peak"
    project.add_theme(dawn_theme)
    project.apply_theme_to_set_list("Dawn to Dusk", set_list.id)
    assert section.name == "Dusk"


def test_apply_theme_leaves_unmapped_sections_unchanged(project, set_list, section, dawn_theme):
    # SectionType.PEAK is mapped; add a GENERAL section that should not be renamed
    from set_manager.models.section import Section

    general_section = Section(name="My General", section_type=SectionType.GENERAL)
    set_list.add_section(general_section)

    project.add_theme(dawn_theme)
    project.apply_theme_to_set_list("Dawn to Dusk", set_list.id)

    assert general_section.name == "My General"  # untouched
    assert section.name == "Dusk"  # renamed


def test_apply_theme_missing_theme_raises(project, set_list):
    with pytest.raises(ValueError, match="Theme"):
        project.apply_theme_to_set_list("Nonexistent", set_list.id)


def test_apply_theme_missing_set_list_raises(project, dawn_theme):
    project.add_theme(dawn_theme)
    with pytest.raises(ValueError, match="Set list"):
        project.apply_theme_to_set_list("Dawn to Dusk", uuid.uuid4())


def test_project_json_round_trip_includes_themes(project, dawn_theme):
    project.add_theme(dawn_theme)
    json_str = project.model_dump_json()
    restored = Project.model_validate_json(json_str)
    assert len(restored.themes) == 1
    assert restored.themes[0].name == "Dawn to Dusk"
    assert restored.themes[0].names[SectionType.OPENER] == "Dawn"
