"""Tests for services/project_io.py."""

import pytest

from set_manager.models.project import Project
from set_manager.services.project_io import ProjectIOError, load_project, save_project


def test_save_and_load_round_trip(project, tmp_path):
    path = tmp_path / "test.setmgr"
    save_project(project, path)
    loaded = load_project(path)

    assert loaded.id == project.id
    assert loaded.name == project.name
    assert set(loaded.tracks.keys()) == set(project.tracks.keys())
    assert len(loaded.set_lists) == len(project.set_lists)
    assert loaded.section_color_map == project.section_color_map


def test_round_trip_preserves_track_data(project, track, tmp_path):
    path = tmp_path / "test.setmgr"
    save_project(project, path)
    loaded = load_project(path)

    restored_track = loaded.tracks[track.id]
    assert restored_track.title == track.title
    assert restored_track.artist == track.artist
    assert restored_track.bpm == track.bpm
    assert restored_track.key == track.key
    assert restored_track.duration == track.duration


def test_round_trip_preserves_section_track_ids(project, track, section, tmp_path):
    path = tmp_path / "test.setmgr"
    save_project(project, path)
    loaded = load_project(path)

    loaded_section = loaded.set_lists[0].sections[0]
    assert track.id in loaded_section.track_ids


def test_round_trip_preserves_set_list_metadata(set_list, tmp_path):
    import datetime

    p = Project(name="P")
    p.add_set_list(set_list)
    path = tmp_path / "test.setmgr"
    save_project(p, path)
    loaded = load_project(path)

    loaded_sl = loaded.set_lists[0]
    assert loaded_sl.name == set_list.name
    assert loaded_sl.date == datetime.date(2024, 6, 1)
    assert loaded_sl.venue == "Berghain"


def test_save_creates_file(project, tmp_path):
    path = tmp_path / "test.setmgr"
    assert not path.exists()
    save_project(project, path)
    assert path.exists()
    assert path.stat().st_size > 0


def test_load_missing_file_raises(tmp_path):
    with pytest.raises(ProjectIOError, match="not found"):
        load_project(tmp_path / "nonexistent.setmgr")


def test_load_wrong_extension_raises(project, tmp_path):
    path = tmp_path / "test.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ProjectIOError, match="extension"):
        load_project(path)


def test_load_invalid_json_raises(tmp_path):
    path = tmp_path / "bad.setmgr"
    path.write_text("not valid json", encoding="utf-8")
    with pytest.raises(ProjectIOError, match="Invalid project file"):
        load_project(path)


def test_load_unsupported_version_raises(project, tmp_path):
    import json

    path = tmp_path / "test.setmgr"
    save_project(project, path)

    data = json.loads(path.read_text(encoding="utf-8"))
    data["version"] = "999"
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ProjectIOError, match="Unsupported"):
        load_project(path)


def test_save_to_nonexistent_directory_raises(project, tmp_path):
    path = tmp_path / "missing_dir" / "test.setmgr"
    with pytest.raises(ProjectIOError, match="Could not write"):
        save_project(project, path)


def test_empty_project_round_trip(tmp_path):
    p = Project(name="Empty")
    path = tmp_path / "empty.setmgr"
    save_project(p, path)
    loaded = load_project(path)
    assert loaded.id == p.id
    assert loaded.name == "Empty"
    assert loaded.tracks == {}
    assert loaded.set_lists == []
