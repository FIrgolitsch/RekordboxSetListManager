"""Wiring-edge capture tests: view/controller signals → observable effects.

Each edge emits the real signal (or triggers the real action path) and asserts
only observable state — model state, window title, statusbar text, menu-action
enablement, and public widget state — never widget internals such as block
maps, scroll position, or collapse flags.
"""

from __future__ import annotations

import csv
import re

import pytest
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox

from rekordbox_set_list_manager.gui.main_window import MainWindow
from rekordbox_set_list_manager.gui.widgets.common.busy_dialog import BusyDialog
from rekordbox_set_list_manager.gui.widgets.dialogs.add_track_dialog import (
    AddTrackDialog,
)
from rekordbox_set_list_manager.gui.widgets.dialogs.section_edit_dialog import (
    SectionEditDialog,
)
from rekordbox_set_list_manager.gui.widgets.dialogs.setlist_export_dialog import (
    SetlistExportDialog,
)
from rekordbox_set_list_manager.models.enums import MatchStatus, SectionType
from rekordbox_set_list_manager.models.section import Section
from rekordbox_set_list_manager.models.track import Track
from rekordbox_set_list_manager.services.project_io import ProjectIOError


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
        "rekordbox_set_list_manager.gui.widgets.dialogs.pick_from_rekordbox_dialog.PickFromRekordboxDialog",
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
        "rekordbox_set_list_manager.gui.widgets.dialogs.fix_match_dialog.FixMatchDialog",
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


def test_save_as_routes_name_sync_through_funnel(window, monkeypatch, tmp_path):
    """Save-As renames the project inside the funnel and emits EditController's signal."""
    # Keep the test hermetic: a real save would record the temp path in the
    # user config's recent-files list.
    monkeypatch.setattr(
        "rekordbox_set_list_manager.controllers.project_controller.add_recent_file",
        lambda path: None,
    )
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda *a, **k: (str(tmp_path / "renamed.setmgr"), ""),
    )
    spy = QSignalSpy(window._edit_ctrl.project_changed)

    window._file_ctrl.save_as()

    assert window._project.name == "renamed"
    assert spy.count() >= 1
    assert window._ctrl.save_path == tmp_path / "renamed.setmgr"
    assert "renamed" in window.windowTitle()


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
    calls = _spy_apply_mutation(window, monkeypatch)

    window._import_ctrl.import_streaming()

    assert len(calls) == 1
    assert window._project.spotify_playlist_id == "pl-1"
    assert window._project.sections[0].track_ids == [t.id for t in tracks]


def _spy_apply_mutation(window, monkeypatch):
    """Wrap apply_mutation so tests can count funneled calls."""
    calls = []
    original = window._edit_ctrl.apply_mutation

    def _spy(fn, **kwargs):
        calls.append(fn)
        return original(fn, **kwargs)

    monkeypatch.setattr(window._edit_ctrl, "apply_mutation", _spy)
    return calls


def test_rematch_manual_routes_through_funnel(window, monkeypatch):
    """The manual re-match batch field-merges inside one funneled mutation."""
    track = Track(title="Sundown", artist="DJ Koze")
    window._project.add_track(track)
    local = Track(
        title="Sundown",
        artist="DJ Koze",
        filepath="/music/sundown.mp3",
        rekordbox_id=42,
    )

    class _FakeRematchDialog:
        DialogCode = QDialog.DialogCode

        def __init__(self, project, parent=None) -> None:
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def pending_matches(self):
            return {track.id: local}

    monkeypatch.setattr(
        "rekordbox_set_list_manager.gui.widgets.dialogs.rematch_dialog.RematchDialog",
        _FakeRematchDialog,
    )
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok)
    calls = _spy_apply_mutation(window, monkeypatch)

    window._rematch_ctrl.rematch_manual()

    assert len(calls) == 1
    assert window._project.get_track(track.id).rekordbox_id == 42


def test_rematch_xml_routes_through_funnel(window, monkeypatch, tmp_path):
    """The collection re-match wholesale-replaces tracks inside one funneled mutation."""
    track = Track(title="Sundown", artist="DJ Koze", isrc="US-RC1-00-00001")
    window._project.add_track(track)

    collection = [
        Track(
            title="Sundown",
            artist="DJ Koze",
            isrc="US-RC1-00-00001",
            rekordbox_id=42,
            filepath="/music/sundown.mp3",
        )
    ]
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        lambda *a, **k: (str(tmp_path / "rekordbox.xml"), ""),
    )
    # The busy dialog normally parses XML on a worker — hand back the
    # fabricated collection directly so no file or DB is needed.
    monkeypatch.setattr(BusyDialog, "run", lambda self, fn: (True, collection, None))
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok)
    calls = _spy_apply_mutation(window, monkeypatch)

    window._rematch_ctrl.rematch_xml()

    assert len(calls) == 1
    matched = window._project.get_track(track.id)
    assert matched.rekordbox_id == 42
    assert matched.match_strategy == "isrc"


# ------------------------------------------------------------- setlist export


def test_export_setlist_action_opens_dialog(window, monkeypatch, section):
    """Export Setlist Files… → SetlistExportDialog with the window's project."""
    window._project.add_section(section)
    constructed = []

    class _FakeSetlistExportDialog:
        DialogCode = QDialog.DialogCode

        def __init__(self, project, parent=None) -> None:
            constructed.append(project)

        def exec(self):
            return QDialog.DialogCode.Rejected

    monkeypatch.setattr(
        "rekordbox_set_list_manager.gui.widgets.dialogs.setlist_export_dialog.SetlistExportDialog",
        _FakeSetlistExportDialog,
    )

    window._act_export_setlist.trigger()

    assert constructed == [window._project]


def test_export_setlist_action_guard_on_empty_project(window, monkeypatch):
    """With no sections the action shows the info guard and never opens a dialog."""
    constructed = []

    class _FakeSetlistExportDialog:
        def __init__(self, *args, **kwargs) -> None:
            constructed.append(args)

        def exec(self):
            return QDialog.DialogCode.Rejected

    monkeypatch.setattr(
        "rekordbox_set_list_manager.gui.widgets.dialogs.setlist_export_dialog.SetlistExportDialog",
        _FakeSetlistExportDialog,
    )
    info_calls = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: info_calls.append(a))

    window._act_export_setlist.trigger()

    assert len(info_calls) == 1
    assert constructed == []


def test_save_as_write_failure_rolls_back_rename(window, monkeypatch, tmp_path):
    """A failed write after the optimistic Save-As rename leaves no trace."""
    original_name = window._project.name
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda *a, **k: (str(tmp_path / "renamed.setmgr"), ""),
    )

    def _fail_save(project, path):
        raise ProjectIOError("disk full")

    monkeypatch.setattr(
        "rekordbox_set_list_manager.controllers.project_controller.save_project",
        _fail_save,
    )
    critical_calls = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: critical_calls.append(a))

    window._file_ctrl.save_as()

    assert window._project.name == original_name
    assert window._ctrl.save_path is None
    assert not window._edit_ctrl.can_undo  # the rename entry was consumed, not kept
    assert not window._edit_ctrl.can_redo  # no phantom rename available via Redo
    assert not window._ctrl.dirty  # state is identical to before the failed save
    assert len(critical_calls) == 1
    assert not (tmp_path / "renamed.setmgr").exists()

    # A subsequent plain Save re-prompts rather than writing to the stale path.
    writes = []
    monkeypatch.setattr(
        "rekordbox_set_list_manager.controllers.project_controller.save_project",
        lambda project, path: writes.append(path),
    )
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: ("", ""))
    window._file_ctrl.save()
    assert writes == []


def test_export_setlist_dialog_writes_real_artifacts(window, monkeypatch, tmp_path, section):
    """The real dialog writes all formats under one basename with valid content."""
    t1 = Track(
        title="Sundown",
        artist="DJ Koze",
        key="Am",
        bpm=128,
        duration=390,
        filepath="/music/sundown.mp3",
    )
    t2 = Track(title="Atlas", artist="Bicep", duration=300, spotify_id="abc123")
    t3 = Track(title="Ghost", artist="Nobody")  # no path or URI — skipped in M3U8
    for t in (t1, t2, t3):
        window._project.add_track(t)
        section.add_track(t.id)
    window._project.add_section(section)

    dialog = SetlistExportDialog(window._project, window)
    dialog._dir_edit.setText(str(tmp_path))
    details = []
    monkeypatch.setattr(
        QMessageBox,
        "exec",
        lambda self: details.append(self.detailedText()) or QMessageBox.StandardButton.Ok,
    )
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)

    dialog._on_export()

    files = {p.suffix: p for p in tmp_path.iterdir()}
    assert set(files) == {".txt", ".csv", ".m3u8"}
    (stem,) = {p.stem for p in files.values()}  # one shared timestamped basename
    assert re.fullmatch(r".+_\d{4}-\d{2}-\d{2}_\d{6}", stem)

    with files[".csv"].open(newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == [
        "section",
        "track_no",
        "artist",
        "title",
        "key",
        "bpm",
        "duration_s",
        "match_status",
        "filepath",
        "spotify_id",
        "tidal_id",
    ]
    assert len(rows) == 4  # header + one row per track (skips are M3U8-only)

    m3u = files[".m3u8"].read_text(encoding="utf-8").splitlines()
    assert m3u[0] == "#EXTM3U"
    assert "/music/sundown.mp3" in m3u
    assert "spotify:track:abc123" in m3u
    assert all(line.startswith("#") or "/" in line or ":" in line for line in m3u[1:])

    # Renderer joins artist/title with an EN DASH on purpose (noqa in source).
    assert "1. DJ Koze – Sundown (Am, 128, 6:30)" in files[".txt"].read_text(  # noqa: RUF001
        encoding="utf-8"
    )

    # Skipped tracks surface through the details button on the result popup.
    assert any("Ghost" in d and "no playable path" in d for d in details)
