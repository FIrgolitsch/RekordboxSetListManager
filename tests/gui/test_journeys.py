"""Golden-path journey tests: whole user flows through a real MainWindow.

Each journey drives the wired signal and dialog paths end to end — import,
organize, note, undo, save, reload — asserting only model state, signal
emissions, and file round-trips. Modal dialogs are stubbed at the module
namespace or via patched ``exec``; assertions never touch widget internals.
"""

from __future__ import annotations

import pytest
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox

from rekordbox_set_list_manager.gui.main_window import MainWindow
from rekordbox_set_list_manager.models.enums import SectionType, TrackSource
from rekordbox_set_list_manager.models.track import Track


@pytest.fixture
def window(qtbot, monkeypatch):
    # Prevent the "Unsaved changes?" popup on close (qtbot tears down by closing)
    monkeypatch.setattr(
        "rekordbox_set_list_manager.controllers.file_controller.FileController.confirm_discard",
        lambda self: True,
    )
    # Keep the journey hermetic: save/load would otherwise record the temp
    # path in the real user config's recent-files list.
    monkeypatch.setattr(
        "rekordbox_set_list_manager.controllers.project_controller.add_recent_file",
        lambda path: None,
    )
    w = MainWindow()
    qtbot.addWidget(w)
    return w


@pytest.fixture(autouse=True)
def _no_modal_popups(monkeypatch):
    """Modal message boxes would block the offscreen run — stub them out."""
    monkeypatch.setattr(
        QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok
    )
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Ok
    )
    monkeypatch.setattr(
        QMessageBox, "critical", lambda *a, **k: QMessageBox.StandardButton.Ok
    )


# --------------------------------------------------------------------------- helpers


def _undo_depth(window) -> int:
    """Current undo-stack depth (the observable unit of user-perceived history)."""
    return len(window._edit_ctrl._stack._undo)


def _stub_import_dialog(monkeypatch, tracks, **opts) -> None:
    """Patch the lazily-imported ImportDialog with a stub returning *tracks*.

    ``import_streaming`` resolves ``ImportDialog`` inside the call, so patching
    the attribute on its home module is enough.
    """

    class _FakeImportDialog:
        DialogCode = QDialog.DialogCode

        def __init__(self, project, parent=None) -> None:
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def results(self):
            return list(tracks)

        @property
        def sync_mode(self):
            return opts.get("sync_mode", False)

        @property
        def selected_spotify_playlist_id(self):
            return opts.get("spotify_playlist_id")

        @property
        def selected_tidal_playlist_id(self):
            return opts.get("tidal_playlist_id")

    monkeypatch.setattr(
        "rekordbox_set_list_manager.gui.widgets.dialogs.import_dialog.ImportDialog",
        _FakeImportDialog,
    )


# --------------------------------------------------------------------------- journeys


def test_import_note_undo_save_reload(window, monkeypatch, tmp_path):
    """Golden path: import tracks → edit a transition note → undo → save → reload."""
    tracks = [
        Track(
            title="Sundown",
            artist="DJ Koze",
            bpm=128.0,
            source=TrackSource.SPOTIFY,
            spotify_id="sp-1",
        ),
        Track(
            title="Atlas",
            artist="Bicep",
            bpm=124.0,
            source=TrackSource.SPOTIFY,
            spotify_id="sp-2",
        ),
    ]
    _stub_import_dialog(monkeypatch, tracks, spotify_playlist_id="pl-1")

    spy = QSignalSpy(window._edit_ctrl.project_changed)
    undo_before = _undo_depth(window)

    # ── import leg: tracks land in a fresh "Imported" section ────────────
    window._import_ctrl.import_streaming()

    assert spy.count() == 1
    assert _undo_depth(window) - undo_before == 1
    assert window._ctrl.dirty is True
    assert "•" in window.windowTitle()
    assert len(window._project.sections) == 1
    section = window._project.sections[0]
    assert section.name == "Imported"
    assert section.section_type == SectionType.GENERAL
    assert section.track_ids == [t.id for t in tracks]
    assert window._project.spotify_playlist_id == "pl-1"

    # ── note leg: wired about_to_modify → note_edit writes one undo step ─
    window._transition_note.about_to_modify.emit()
    window._transition_note.note_edit.emit(section.id, tracks[0].id, "smooth blend")

    assert section.transition_notes[str(tracks[0].id)] == "smooth blend"
    assert _undo_depth(window) - undo_before == 2

    # ── undo leg: restores the post-import, pre-note state ────────────────
    window._edit_ctrl.undo()

    restored = window._project.get_section(section.id)
    assert restored is not None
    assert restored.transition_notes == {}
    assert restored.track_ids == [t.id for t in tracks]

    # ── save leg: real FileController save-as to tmp_path ─────────────────
    save_path = tmp_path / "golden.setmgr"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", lambda *a, **k: (str(save_path), "")
    )
    window._save()

    assert save_path.exists()
    assert window._ctrl.dirty is False
    assert "•" not in window.windowTitle()
    assert window._ctrl.save_path == save_path
    expected = window._project.model_dump_json()

    # ── reload leg: FileController open round-trips the project ───────────
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", lambda *a, **k: (str(save_path), "")
    )
    window._file_ctrl.open()

    reloaded = window._project
    assert reloaded is not None
    assert reloaded.model_dump_json() == expected
    assert window._ctrl.save_path == save_path
    assert window._ctrl.dirty is False
    # Opening a file resets undo history via the file controller's signals.
    assert not window._edit_ctrl.can_undo
