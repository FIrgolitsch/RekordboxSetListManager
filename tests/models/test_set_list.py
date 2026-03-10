"""Tests for models/set_list.py."""

import datetime
import uuid

import pytest

from set_manager.models.section import Section
from set_manager.models.set_list import SetList


def test_set_list_defaults():
    sl = SetList(name="My Set")
    assert isinstance(sl.id, uuid.UUID)
    assert sl.date is None
    assert sl.venue is None
    assert sl.sections == []
    assert sl.total_track_count == 0


def test_add_section(set_list, section):
    assert section in set_list.sections


def test_remove_section(set_list, section):
    set_list.remove_section(section.id)
    assert set_list.sections == []


def test_remove_nonexistent_section_is_noop(set_list):
    original_count = len(set_list.sections)
    set_list.remove_section(uuid.uuid4())
    assert len(set_list.sections) == original_count


def test_get_section(set_list, section):
    found = set_list.get_section(section.id)
    assert found is section


def test_get_section_missing(set_list):
    assert set_list.get_section(uuid.uuid4()) is None


def test_move_section():
    sl = SetList(name="Test")
    secs = [Section(name=f"S{i}") for i in range(3)]
    for s in secs:
        sl.add_section(s)
    sl.move_section(secs[2].id, 0)
    assert sl.sections == [secs[2], secs[0], secs[1]]


def test_move_section_not_found():
    sl = SetList(name="Test")
    with pytest.raises(ValueError):
        sl.move_section(uuid.uuid4(), 0)


def test_all_track_ids(set_list, section, track):
    assert track.id in set_list.all_track_ids


def test_total_track_count(set_list):
    assert set_list.total_track_count == 1


def test_set_list_json_round_trip(set_list):
    json_str = set_list.model_dump_json()
    restored = SetList.model_validate_json(json_str)
    assert restored == set_list
    assert restored.date == datetime.date(2024, 6, 1)
    assert restored.venue == "Berghain"
