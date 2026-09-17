"""Wiring-edge capture tests: view/controller signals → observable effects.

Each edge emits the real signal (or triggers the real action path) and asserts
only observable state — model state, window title, statusbar text, menu-action
enablement, and public widget state — never widget internals such as block
maps, scroll position, or collapse flags.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QDialog, QMessageBox

from rekordbox_set_list_manager.gui.main_window import MainWindow
from rekordbox_set_list_manager.gui.widgets.dialogs.add_track_dialog import (
    AddTrackDialog,
)
from rekordbox_set_list_manager.gui.widgets.dialogs.section_edit_dialog import (
    SectionEditDialog,
)
from rekordbox_set_list_manager.models.enums import MatchStatus, SectionType
from rekordbox_set_list_manager.models.section import Section
from rekordbox_set_list_manager.models.track import Track


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


# ----------------------------------------------------------------- dirty / title


def test_mutation_marks_dirty_and_updates_title(window):
    assert window._ctrl.dirty is False
    assert "•" not in window.windowTitle()

    window._edit_ctrl.add_section(Section(name="Opening", section_type=SectionType.GENERAL))

    assert window._ctrl.dirty is True
    assert "•" in window.windowTitle()


def test_undo_action_enablement_follows_stack(window):
    assert not window._act_undo.isEnabled()
    assert not window._act_redo.isEnabled()

    window._edit_ctrl.add_section(Section(name="Opening", section_type=SectionType.GENERAL))
    assert window._act_undo.isEnabled()
    assert not window._act_redo.isEnabled()

    window._edit_ctrl.undo()
    assert not window._act_undo.isEnabled()
    assert window._act_redo.isEnabled()


# -------------------------------------------------------------------- statusbar


def test_section_modified_updates_statusbar(window, track, section):
    window._project.add_track(track)
    window._project.add_section(section)
    assert window._status_tracks.text() == "Tracks: 0"

    window._multi_section_view.section_modified.emit()

    assert window._status_tracks.text() == "Tracks: 1"
    assert window._status_duration.text() == "Duration: 6:30"
    assert window._status_unmatched.text() == "Unmatched: 1"


def test_view_project_changed_updates_statusbar(window, track, section):
    window._project.add_track(track)
    window._project.add_section(section)
    assert window._status_tracks.text() == "Tracks: 0"

    window._multi_section_view.project_changed.emit()

    assert window._status_tracks.text() == "Tracks: 1"


# -------------------------------------------------------------- track selection


def test_track_selected_populates_panels(window, track, section):
    window._project.add_track(track)
    window._project.add_section(section)
    assert not window._transition_note._edit.isEnabled()

    window._multi_section_view.track_selected.emit(track.id, section)

    assert window._transition_note._edit.isEnabled()
    assert window._match_info._vals["Status"].text() == "Unmatched"
    assert window._match_info._vals["Source"].text() == "Manual"


def test_track_deselected_clears_panels(window, track, section):
    window._project.add_track(track)
    window._project.add_section(section)
    window._multi_section_view.track_selected.emit(track.id, section)

    window._multi_section_view.track_selected.emit(None, None)

    assert not window._transition_note._edit.isEnabled()
    assert window._match_info._vals["Status"].text() == "—"


# -------------------------------------------------------------- request signals


def test_add_track_requested_routes_through_dialog(window, monkeypatch, section):
    window._project.add_section(section)
    track = Track(title="Strobe", artist="Deadmau5")
    monkeypatch.setattr(AddTrackDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(AddTrackDialog, "track", lambda self: track)

    window._multi_section_view.add_track_requested.emit(section)

    assert window._project.get_track(track.id) is track
    assert track.id in section.track_ids


def test_add_from_rekordbox_requested_routes_through_dialog(window, monkeypatch, section):
    window._project.add_section(section)
    track = Track(title="Atlas", artist="Bicep", rekordbox_id=7)

    class _FakePickDialog:
        DialogCode = QDialog.DialogCode

        def __init__(self, *args, **kwargs) -> None:
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def track(self):
            return track

    # The real dialog probes the Rekordbox DB in its constructor — stub it out.
    monkeypatch.setattr(
        "rekordbox_set_list_manager.gui.main_window.PickFromRekordboxDialog",
        _FakePickDialog,
    )
    window._multi_section_view.add_from_rekordbox_requested.emit(section)

    assert window._project.get_track(track.id) is track
    assert track.id in section.track_ids


def test_fix_match_requested_applies_match(window, monkeypatch, track, section):
    window._project.add_track(track)
    window._project.add_section(section)
    local = Track(
        title="Sundown",
        artist="DJ Koze",
        filepath="/music/sundown.mp3",
        rekordbox_id=42,
    )

    class _FakeFixMatchDialog:
        DialogCode = QDialog.DialogCode

        def __init__(self, *args, **kwargs) -> None:
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def matched_local_track(self):
            return local

    # The real dialog builds a collection browser — stub it out.
    monkeypatch.setattr(
        "rekordbox_set_list_manager.gui.main_window.FixMatchDialog",
        _FakeFixMatchDialog,
    )
    window._multi_section_view.fix_match_requested.emit(track.id, section)

    assert track.match_status == MatchStatus.MANUALLY_MATCHED
    assert track.rekordbox_id == 42


def test_import_requested_opens_import_dialog(window, monkeypatch):
    opened = []

    class _FakeImportDialog:
        DialogCode = QDialog.DialogCode

        def __init__(self, project, parent=None) -> None:
            opened.append(project)

        def exec(self):
            return QDialog.DialogCode.Rejected

    # The real dialog constructs streaming service clients — stub it out.
    monkeypatch.setattr(
        "rekordbox_set_list_manager.gui.widgets.dialogs.import_dialog.ImportDialog",
        _FakeImportDialog,
    )
    window._multi_section_view.import_requested.emit()

    assert opened == [window._project]


# ------------------------------------------------------------------ empty state


def test_empty_banner_shown_on_empty_project(window):
    assert not window._multi_section_view._empty_banner.isHidden()


def test_empty_banner_hidden_after_first_section(window, monkeypatch):
    banner = window._multi_section_view._empty_banner
    assert not banner.isHidden()

    monkeypatch.setattr(SectionEditDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(SectionEditDialog, "section_name", lambda self: "Opening")
    monkeypatch.setattr(SectionEditDialog, "section_type", lambda self: SectionType.OPENER)

    window._multi_section_view.add_section_interactive()

    assert banner.isHidden()
    assert len(window._project.sections) == 1


def test_project_changed_signal_reloads_view(window, section):
    window._project.add_section(section)
    assert not window._multi_section_view._empty_banner.isHidden()

    window._ctrl.project_changed.emit(window._project)

    assert window._multi_section_view._empty_banner.isHidden()


# ------------------------------------------------------------- file-controller


def test_note_cleared_resets_transition_note(window, track, section):
    window._project.add_track(track)
    window._project.add_section(section)
    window._multi_section_view.track_selected.emit(track.id, section)
    assert window._transition_note._edit.isEnabled()

    window._file_ctrl.note_cleared.emit()

    assert not window._transition_note._edit.isEnabled()
    assert window._transition_note._track_id is None


def test_undo_cleared_disables_undo(window):
    window._edit_ctrl.add_section(Section(name="S", section_type=SectionType.GENERAL))
    assert window._act_undo.isEnabled()

    window._file_ctrl.undo_cleared.emit()

    assert not window._edit_ctrl.can_undo
    assert not window._act_undo.isEnabled()


# -------------------------------------------------------------- funnel routing


def test_import_batch_routes_through_funnel(window, monkeypatch):
    """The whole import batch — tracks, section, playlist id — is one funneled mutation."""
    tracks = [
        Track(title="Sundown", artist="DJ Koze"),
        Track(title="Atlas", artist="Bicep"),
    ]

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
            return False

        @property
        def selected_spotify_playlist_id(self):
            return "pl-1"

        @property
        def selected_tidal_playlist_id(self):
            return None

    monkeypatch.setattr(
        "rekordbox_set_list_manager.gui.widgets.dialogs.import_dialog.ImportDialog",
        _FakeImportDialog,
    )
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok)
    calls = []
    original = window._edit_ctrl.apply_mutation

    def _spy(fn, **kwargs):
        calls.append(fn)
        return original(fn, **kwargs)

    monkeypatch.setattr(window._edit_ctrl, "apply_mutation", _spy)

    window._import_ctrl.import_streaming()

    assert len(calls) == 1
    assert window._project.spotify_playlist_id == "pl-1"
    assert window._project.sections[0].track_ids == [t.id for t in tracks]
