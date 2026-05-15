"""Unit tests for ProjectController."""

from __future__ import annotations

from pathlib import Path  # noqa: TC003 — used at runtime in test assertions
from unittest.mock import patch

import pytest

from rekordbox_set_list_manager.controllers.project_controller import ProjectController
from rekordbox_set_list_manager.models.project import Project
from rekordbox_set_list_manager.services.project_io import ProjectIOError

_LOAD = "rekordbox_set_list_manager.controllers.project_controller.load_project"
_SAVE = "rekordbox_set_list_manager.controllers.project_controller.save_project"
_RECENT = "rekordbox_set_list_manager.controllers.project_controller.add_recent_file"


def _make_ctrl() -> ProjectController:
    """Return a bare (no Qt parent) ProjectController."""
    return ProjectController()


def test_initial_state():
    ctrl = _make_ctrl()
    assert ctrl.project is None
    assert ctrl.save_path is None
    assert ctrl.dirty is False


def test_new_creates_project():
    ctrl = _make_ctrl()
    project = ctrl.new()
    assert isinstance(project, Project)
    assert ctrl.project is project


def test_new_clears_save_path(tmp_path):
    ctrl = _make_ctrl()
    ctrl._save_path = tmp_path / "old.setmgr"
    ctrl.new()
    assert ctrl.save_path is None


def test_new_clears_dirty():
    ctrl = _make_ctrl()
    ctrl._dirty = True
    ctrl.new()
    assert ctrl.dirty is False


def test_new_emits_project_changed():
    ctrl = _make_ctrl()
    received = []
    ctrl.project_changed.connect(received.append)
    ctrl.new()
    assert len(received) == 1
    assert isinstance(received[0], Project)


def test_new_emits_dirty_changed_when_was_dirty():
    ctrl = _make_ctrl()
    ctrl._dirty = True
    events: list[bool] = []
    ctrl.dirty_changed.connect(events.append)
    ctrl.new()
    assert False in events  # dirty -> False fired


def test_load_sets_project_and_path(tmp_path):
    ctrl = _make_ctrl()
    project = Project(name="Test")
    save_file = tmp_path / "test.setmgr"

    with (
        patch(_LOAD, return_value=project) as mock_load,
        patch(_RECENT) as mock_recent,
    ):
        result = ctrl.load(save_file)

    mock_load.assert_called_once_with(save_file)
    mock_recent.assert_called_once_with(str(save_file))
    assert result is project
    assert ctrl.project is project
    assert ctrl.save_path == save_file
    assert ctrl.dirty is False


def test_load_emits_project_changed(tmp_path):
    ctrl = _make_ctrl()
    project = Project(name="Loaded")
    received = []
    ctrl.project_changed.connect(received.append)

    with patch(_LOAD, return_value=project), patch(_RECENT):
        ctrl.load(tmp_path / "x.setmgr")

    assert received == [project]


def test_load_propagates_project_io_error(tmp_path):
    ctrl = _make_ctrl()
    with (
        patch(_LOAD, side_effect=ProjectIOError("bad file")),
        pytest.raises(ProjectIOError, match="bad file"),
    ):
        ctrl.load(tmp_path / "bad.setmgr")

    assert ctrl.project is None
    assert ctrl.save_path is None


def test_save_writes_and_clears_dirty(tmp_path):
    ctrl = _make_ctrl()
    ctrl._project = Project(name="ToSave")
    ctrl._dirty = True
    save_file = tmp_path / "out.setmgr"

    with patch(_SAVE) as mock_save, patch(_RECENT) as mock_recent:
        ctrl.save(save_file)

    mock_save.assert_called_once()
    mock_recent.assert_called_once_with(str(save_file))
    assert ctrl.save_path == save_file
    assert ctrl.dirty is False


def test_save_normalises_extension(tmp_path):
    ctrl = _make_ctrl()
    ctrl._project = Project(name="ToSave")

    with patch(_SAVE) as mock_save, patch(_RECENT):
        ctrl.save(tmp_path / "out.txt")

    saved_path: Path = mock_save.call_args[0][1]
    assert saved_path.suffix == ".setmgr"


def test_save_no_op_when_no_project(tmp_path):
    ctrl = _make_ctrl()
    with patch(_SAVE) as mock_save:
        ctrl.save(tmp_path / "whatever.setmgr")
    mock_save.assert_not_called()


def test_save_propagates_project_io_error(tmp_path):
    ctrl = _make_ctrl()
    ctrl._project = Project(name="X")

    with (
        patch(_SAVE, side_effect=ProjectIOError("disk full")),
        patch(_RECENT),
        pytest.raises(ProjectIOError, match="disk full"),
    ):
        ctrl.save(tmp_path / "x.setmgr")


def test_mark_dirty_sets_flag():
    ctrl = _make_ctrl()
    ctrl.new()
    assert ctrl.dirty is False
    ctrl.mark_dirty()
    assert ctrl.dirty is True


def test_mark_dirty_emits_signal_once():
    ctrl = _make_ctrl()
    events: list[bool] = []
    ctrl.dirty_changed.connect(events.append)
    ctrl.mark_dirty()
    ctrl.mark_dirty()  # already dirty - no second signal
    assert events == [True]


def test_restore_replaces_project_and_marks_dirty():
    ctrl = _make_ctrl()
    ctrl.new()
    original = ctrl.project
    snap = Project(name="Snapped")

    ctrl.restore(snap)

    assert ctrl.project is snap
    assert ctrl.project is not original
    assert ctrl.dirty is True


def test_restore_emits_project_changed():
    ctrl = _make_ctrl()
    ctrl.new()
    received = []
    ctrl.project_changed.connect(received.append)
    snap = Project(name="Snap2")
    ctrl.restore(snap)
    assert received == [snap]
