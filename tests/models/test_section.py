"""Tests for models/section.py."""

import uuid

import pytest

from rekordbox_set_list_manager.models.enums import RekordboxColor, SectionType
from rekordbox_set_list_manager.models.section import Section


def test_section_defaults():
    s = Section(name="Intro")
    assert isinstance(s.id, uuid.UUID)
    assert s.section_type == SectionType.GENERAL
    assert s.color == RekordboxColor.NONE
    assert s.track_ids == []
    assert s.track_count == 0


def test_add_track(section, track):
    assert track.id in section.track_ids
    assert section.track_count == 1


def test_add_track_no_duplicates(section, track):
    section.add_track(track.id)
    assert section.track_ids.count(track.id) == 1


def test_remove_track(section, track):
    section.remove_track(track.id)
    assert track.id not in section.track_ids
    assert section.track_count == 0


def test_remove_nonexistent_track_is_noop(section):
    section.remove_track(uuid.uuid4())
    assert section.track_count == 1


def test_move_track():
    s = Section(name="Mix")
    ids = [uuid.uuid4() for _ in range(3)]
    for tid in ids:
        s.add_track(tid)
    # Move last to first
    s.move_track(ids[2], 0)
    assert s.track_ids == [ids[2], ids[0], ids[1]]


def test_move_track_not_in_section():
    s = Section(name="Mix")
    with pytest.raises(ValueError, match="not in section"):
        s.move_track(uuid.uuid4(), 0)


def test_section_json_round_trip(section):
    json_str = section.model_dump_json()
    restored = Section.model_validate_json(json_str)
    assert restored == section
    assert restored.track_ids == section.track_ids
    assert restored.color == RekordboxColor.RED
