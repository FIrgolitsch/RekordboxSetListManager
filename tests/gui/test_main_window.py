"""GUI smoke tests for MainWindow."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QMessageBox

from rekordbox_set_list_manager.gui.main_window import MainWindow
from rekordbox_set_list_manager.models.enums import RekordboxColor, SectionType
from rekordbox_set_list_manager.models.project import Project
from rekordbox_set_list_manager.models.section import Section
from rekordbox_set_list_manager.models.track import Track
from rekordbox_set_list_manager.services.project_io import load_project, save_project


@pytest.fixture
def window(qtbot, monkeypatch):
    # Prevent the "Unsaved changes?" popup on close (qtbot tears down by closing)
    monkeypatch.setattr(
        "rekordbox_set_list_manager.controllers.file_controller.FileController.confirm_discard",
        lambda self: True,
    )
    w = MainWindow()
    qtbot.addWidget(w)
    return w


def test_opens_with_empty_project(window):
    assert window._project is not None
    assert window._project.name == "Untitled"
    assert window._project.sections == []


def test_window_title_contains_app_name(window):
    assert "Rekordbox Set List Manager" in window.windowTitle()


def test_add_section_updates_project(window):
    section = Section(name="Peak", section_type=SectionType.PEAK, color=RekordboxColor.RED)
    window._edit_ctrl.add_section(section)

    assert len(window._project.sections) == 1
    assert window._project.sections[0].name == "Peak"


def test_undo_removes_added_section(window):
    section = Section(name="Opening", section_type=SectionType.GENERAL)
    window._edit_ctrl.add_section(section)
    assert len(window._project.sections) == 1
    assert window._edit_ctrl.can_undo

    window._undo()

    assert len(window._project.sections) == 0


def test_redo_reapplies_undone_section(window):
    section = Section(name="Closing", section_type=SectionType.GENERAL)
    window._edit_ctrl.add_section(section)
    window._undo()
    assert window._edit_ctrl.can_redo

    window._redo()

    assert len(window._project.sections) == 1
    assert window._project.sections[0].name == "Closing"


def test_save_reload_roundtrip(window, tmp_path):
    track = Track(title="Sunrise", artist="Lane 8", bpm=122.0)
    section = Section(name="Opening", section_type=SectionType.GENERAL)
    section.add_track(track.id)

    proj = window._project
    proj.add_track(track)
    proj.add_section(section)

    save_path = tmp_path / "test.setmgr"
    save_project(proj, save_path)

    reloaded = load_project(save_path)

    assert len(reloaded.sections) == 1
    assert reloaded.sections[0].name == "Opening"
    assert reloaded.sections[0].track_ids == [track.id]
    assert reloaded.tracks[track.id].title == "Sunrise"
    assert reloaded.tracks[track.id].artist == "Lane 8"


def test_autosave_recovery_accept_leaves_view_coherent(window, monkeypatch, tmp_path):
    """Accepting a newer-autosave prompt leaves the view on the recovered project.

    ``ctrl.restore()`` re-emits ``project_changed`` inside the recovery slot —
    a stale outer emission must not repaint the view from the superseded
    on-disk project.
    """
    # Keep the test hermetic: save/load would otherwise record the temp path
    # in the real user config's recent-files list.
    monkeypatch.setattr(
        "rekordbox_set_list_manager.controllers.project_controller.add_recent_file",
        lambda path: None,
    )
    save_path = tmp_path / "proj.setmgr"
    window._ctrl.save(save_path)

    recovered = Project(name="Recovered")
    recovered.add_section(Section(name="Recovered Section", section_type=SectionType.PEAK))

    monkeypatch.setattr(
        "rekordbox_set_list_manager.controllers.autosave_controller.autosave_mtime",
        lambda project_id: float("inf"),
    )
    monkeypatch.setattr(
        "rekordbox_set_list_manager.controllers.autosave_controller.read_autosave",
        lambda project_id: recovered,
    )
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)

    window._ctrl.load(save_path)

    assert window._project is recovered
    block_names = {block.section.name for block in window._multi_section_view._blocks.values()}
    assert "Recovered Section" in block_names
