"""Golden-path journey tests: whole user flows through a real MainWindow.

Each journey drives the wired signal and dialog paths end to end — import,
organize, note, undo, save, reload — asserting only model state, signal
emissions, and file round-trips. Modal dialogs are stubbed at the module
namespace or via patched ``exec``; assertions never touch widget internals.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox

from rekordbox_set_list_manager.gui.main_window import MainWindow
from rekordbox_set_list_manager.gui.widgets.common.busy_dialog import BusyDialog
from rekordbox_set_list_manager.gui.widgets.dialogs.export_dialog import ExportDialog
from rekordbox_set_list_manager.models.enums import (
    MatchStatus,
    RekordboxColor,
    SectionType,
    TrackSource,
)
from rekordbox_set_list_manager.models.section import Section
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
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: QMessageBox.StandardButton.Ok)


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
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(save_path), ""))
    window._save()

    assert save_path.exists()
    assert window._ctrl.dirty is False
    assert "•" not in window.windowTitle()
    assert window._ctrl.save_path == save_path
    expected = window._project.model_dump_json()

    # ── reload leg: FileController open round-trips the project ───────────
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(save_path), ""))
    window._file_ctrl.open()

    reloaded = window._project
    assert reloaded is not None
    assert reloaded.model_dump_json() == expected
    assert window._ctrl.save_path == save_path
    assert window._ctrl.dirty is False
    # Opening a file resets undo history via the file controller's signals.
    assert not window._edit_ctrl.can_undo


def test_organize_move_reorder_delete_undo_chain(window):
    """Build sections, move a track across them, reorder, delete — undo each step."""
    warmup = Section(name="Warmup", section_type=SectionType.WARM_UP)
    peak = Section(name="Peak", section_type=SectionType.PEAK)
    t1 = Track(title="Intro", artist="Artist A")
    t2 = Track(title="Builder", artist="Artist B")
    t3 = Track(title="Banger", artist="Artist C")

    spy = QSignalSpy(window._edit_ctrl.project_changed)
    undo_before = _undo_depth(window)

    window._edit_ctrl.add_section(warmup)
    window._edit_ctrl.add_section(peak)
    window._edit_ctrl.add_track(t1, warmup.id)
    window._edit_ctrl.add_track(t2, warmup.id)
    window._edit_ctrl.add_track(t3, peak.id)

    # ── move leg: t1 crosses sections in one batch step ───────────────────
    window._edit_ctrl.move_tracks_batch([t1.id], warmup.id, peak.id, 0)
    assert warmup.track_ids == [t2.id]
    assert peak.track_ids == [t1.id, t3.id]

    # ── reorder leg: in-section ordering ──────────────────────────────────
    window._edit_ctrl.reorder_section_tracks(peak.id, [t3.id, t1.id])
    assert peak.track_ids == [t3.id, t1.id]

    # ── delete leg: a whole section goes away ─────────────────────────────
    window._edit_ctrl.remove_section(warmup.id)
    assert [s.name for s in window._project.sections] == ["Peak"]

    # Eight funnelled mutations → eight emissions, eight undo steps, dirty.
    assert spy.count() == 8
    assert _undo_depth(window) - undo_before == 8
    assert window._ctrl.dirty is True
    assert "•" in window.windowTitle()

    # ── undo chain restores each step in reverse ──────────────────────────
    window._edit_ctrl.undo()
    assert [s.name for s in window._project.sections] == ["Warmup", "Peak"]
    assert window._project.get_section(warmup.id).track_ids == [t2.id]

    window._edit_ctrl.undo()
    assert window._project.get_section(peak.id).track_ids == [t1.id, t3.id]

    window._edit_ctrl.undo()
    assert window._project.get_section(warmup.id).track_ids == [t1.id, t2.id]
    assert window._project.get_section(peak.id).track_ids == [t3.id]
    assert window._edit_ctrl.can_undo  # add_track/add_section steps remain


def test_rematch_xml_journey(window, monkeypatch, tmp_path):
    """Re-match flow: XML picker → collection load → unmatched track gains a match."""
    track = Track(title="Sundown", artist="DJ Koze", isrc="US-RC1-00-00001")
    section = Section(name="Peak", section_type=SectionType.PEAK)
    section.add_track(track.id)
    window._project.add_track(track)
    window._project.add_section(section)
    window._ctrl.project_changed.emit(window._project)  # rebuild view blocks
    assert window._ctrl.dirty is False
    assert track.match_status == MatchStatus.UNMATCHED

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
    # The busy dialog normally runs the XML parse on a worker; hand back the
    # fabricated collection directly so no file or DB is needed.
    monkeypatch.setattr(BusyDialog, "run", lambda self, fn: (True, collection, None))

    spy = QSignalSpy(window._edit_ctrl.project_changed)
    undo_before = _undo_depth(window)

    window._rematch_ctrl.rematch_xml()

    assert spy.count() == 1
    assert _undo_depth(window) - undo_before == 1
    assert window._ctrl.dirty is True
    matched = window._project.get_track(track.id)
    assert matched.match_status == MatchStatus.MATCHED
    assert matched.rekordbox_id == 42
    assert matched.filepath == "/music/sundown.mp3"
    assert matched.match_strategy == "isrc"

    # Undo restores the unmatched track.
    window._edit_ctrl.undo()
    restored = window._project.get_track(track.id)
    assert restored.match_status == MatchStatus.UNMATCHED
    assert restored.rekordbox_id is None


def test_fix_match_then_export_xml_journey(window, monkeypatch, tmp_path):
    """Fix-match flow → export: a manual match lands, then the set writes XML."""
    track = Track(title="Sundown", artist="DJ Koze")
    section = Section(name="Peak", section_type=SectionType.PEAK, color=RekordboxColor.RED)
    section.add_track(track.id)
    window._project.add_track(track)
    window._project.add_section(section)
    window._ctrl.project_changed.emit(window._project)

    local = Track(
        title="Sundown",
        artist="DJ Koze",
        filepath="/music/sundown.mp3",
        rekordbox_id=42,
        bpm=127.5,
        key="Am",
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

    spy = QSignalSpy(window._edit_ctrl.project_changed)
    window._multi_section_view.fix_match_requested.emit(track.id, section)

    assert spy.count() == 1
    assert track.match_status == MatchStatus.MANUALLY_MATCHED
    assert track.rekordbox_id == 42
    assert window._ctrl.dirty is True

    # ── export leg: real ExportDialog driven through its XML path ────────
    xml_path = tmp_path / "peak_set.xml"
    # Keep the DB probe out of the test entirely so the export can never
    # reach a real Rekordbox database.
    monkeypatch.setattr(
        "rekordbox_set_list_manager.gui.widgets.dialogs.export_dialog"
        ".RekordboxDbService.is_available",
        lambda self: False,
    )

    def _drive_export(dialog):
        dialog._path_edit.setText(str(xml_path))
        dialog._on_export()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(ExportDialog, "exec", _drive_export)
    window._export()

    assert xml_path.exists()
    root = ET.parse(xml_path).getroot()
    track_names = {el.get("Name") for el in root.iter("TRACK")}
    assert "Sundown" in track_names
    playlist_names = {node.get("Name") for node in root.iter("NODE") if node.get("Type") == "1"}
    assert "Peak" in playlist_names
    # The section name is stamped into each exported track's Comments.
    comments = {el.get("Comments") for el in root.iter("TRACK")}
    assert "Peak" in comments
