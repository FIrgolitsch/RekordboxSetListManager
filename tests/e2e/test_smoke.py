"""E2E smoke tests: verify the full application stack initialises correctly.

These tests start a real MainWindow (with Qt's offscreen platform backend)
and assert that every major subsystem is wired up before a single user action
has been taken.  They complement the unit tests in ``tests/gui/`` by exercising
the full controller/widget integration path.
"""

from __future__ import annotations

import pytest

from rekordbox_set_list_manager.gui.main_window import MainWindow

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def window(qtbot, monkeypatch):
    """Return a visible MainWindow with all file-interaction dialogs suppressed."""
    monkeypatch.setattr(
        "rekordbox_set_list_manager.controllers.file_controller.FileController.confirm_discard",
        lambda self: True,
    )
    w = MainWindow()
    qtbot.addWidget(w)
    w.show()
    return w


# ---------------------------------------------------------------------------
# App startup
# ---------------------------------------------------------------------------


def test_window_is_visible(window):
    """The main window is visible after construction."""
    assert window.isVisible()


def test_window_title_contains_app_name(window):
    """The window title identifies the application."""
    assert "Rekordbox Set List Manager" in window.windowTitle()


def test_project_controller_initialised(window):
    """ProjectController is present and holds an in-memory project on startup."""
    assert window._ctrl is not None
    assert window._ctrl.project is not None


def test_fresh_project_has_no_sections(window):
    """A brand-new project has an empty section list."""
    assert window._ctrl.project.sections == []


def test_edit_controller_has_clean_undo_history(window):
    """EditController is present with no pending undo or redo actions."""
    assert window._edit_ctrl is not None
    assert not window._edit_ctrl.can_undo
    assert not window._edit_ctrl.can_redo


def test_match_info_widget_present(window):
    """The MatchInfoWidget panel is initialised inside the main window."""
    assert window._match_info is not None


# ---------------------------------------------------------------------------
# Basic undo/redo round-trip
# ---------------------------------------------------------------------------


def test_add_section_then_undo(window):
    """Adding a section creates an undo entry; undoing removes the section."""
    from rekordbox_set_list_manager.models.section import Section

    section = Section(name="Warmup")
    window._edit_ctrl.add_section(section)

    assert len(window._ctrl.project.sections) == 1
    assert window._edit_ctrl.can_undo

    window._edit_ctrl.undo()

    assert window._ctrl.project.sections == []
    assert not window._edit_ctrl.can_undo


def test_undo_then_redo_restores_section(window):
    """Redoing an undone add_section brings the section back."""
    from rekordbox_set_list_manager.models.section import Section

    section = Section(name="Peak")
    window._edit_ctrl.add_section(section)
    window._edit_ctrl.undo()

    assert window._edit_ctrl.can_redo
    window._edit_ctrl.redo()

    assert len(window._ctrl.project.sections) == 1
    assert not window._edit_ctrl.can_redo
