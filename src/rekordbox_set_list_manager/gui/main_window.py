"""Main application window."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QLabel,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QTabWidget,
)

from rekordbox_set_list_manager.controllers.edit_controller import EditController
from rekordbox_set_list_manager.controllers.file_controller import FileController
from rekordbox_set_list_manager.controllers.import_controller import ImportController
from rekordbox_set_list_manager.controllers.project_controller import ProjectController
from rekordbox_set_list_manager.controllers.rematch_controller import RematchController
from rekordbox_set_list_manager.controllers.spotify_sync_controller import SpotifySyncController
from rekordbox_set_list_manager.gui.widgets.dialogs.add_track_dialog import AddTrackDialog
from rekordbox_set_list_manager.gui.widgets.dialogs.export_dialog import ExportDialog
from rekordbox_set_list_manager.gui.widgets.dialogs.fix_match_dialog import FixMatchDialog
from rekordbox_set_list_manager.gui.widgets.dialogs.settings_dialog import SettingsDialog
from rekordbox_set_list_manager.gui.widgets.dialogs.theme_dialog import ThemeDialog
from rekordbox_set_list_manager.gui.widgets.panels.match_info_widget import MatchInfoWidget
from rekordbox_set_list_manager.gui.widgets.panels.transition_note import TransitionNoteWidget
from rekordbox_set_list_manager.gui.widgets.sections.multi_section_view import MultiSectionView
from rekordbox_set_list_manager.models.enums import MatchStatus
from rekordbox_set_list_manager.models.project import Project
from rekordbox_set_list_manager.models.section import Section
from rekordbox_set_list_manager.services.autosave import (
    autosave_mtime,
    clear_autosave,
    read_autosave,
    write_autosave,
)
from rekordbox_set_list_manager.services.spotify_service import SpotifyService
from rekordbox_set_list_manager.utils.config import get_recent_files

_APP_NAME = "Rekordbox Set List Manager"
_DEFAULT_PROJECT_NAME = "Untitled"


class MainWindow(QMainWindow):
    """Top-level window: multi-section view + transition note."""

    def __init__(self) -> None:
        """Initialise the main window and all sub-controllers."""
        super().__init__()
        self._ctrl = ProjectController(self)
        self._edit_ctrl = EditController(self._ctrl, self)
        self._spotify = SpotifyService()

        self._multi_section_view = MultiSectionView(self._edit_ctrl)
        self._transition_note = TransitionNoteWidget()
        self._match_info = MatchInfoWidget()

        self._bottom_tabs = QTabWidget()
        self._bottom_tabs.addTab(self._transition_note, "Transition Note")
        self._bottom_tabs.addTab(self._match_info, "Match Info")

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self._multi_section_view)
        splitter.addWidget(self._bottom_tabs)
        splitter.setSizes([600, 120])
        splitter.setChildrenCollapsible(False)
        self.setCentralWidget(splitter)

        # Action controllers
        self._file_ctrl = FileController(self._ctrl, self, self)
        self._import_ctrl = ImportController(
            self._ctrl, self._edit_ctrl, self._multi_section_view, self, self
        )
        self._rematch_ctrl = RematchController(
            self._ctrl, self._edit_ctrl, self._multi_section_view, self, self
        )
        self._spotify_sync_ctrl = SpotifySyncController(self._ctrl, self._spotify, self, self)

        self._setup_menus()
        self._setup_statusbar()
        self._connect_signals()

        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(30_000)
        self._autosave_timer.timeout.connect(self._do_autosave)
        self._autosave_timer.start()

        self.setWindowTitle(_APP_NAME)
        self.setMinimumSize(QSize(700, 500))
        self.resize(1000, 700)

        self._file_ctrl.new_project()

    # ------------------------------------------------------------------ menus

    def _setup_menus(self) -> None:
        bar = self.menuBar()

        self._file_menu = bar.addMenu("&File")
        self._act_new = self._action(
            "&New", QKeySequence.StandardKey.New, self._file_ctrl.new_project
        )
        self._act_open = self._action("&Open…", QKeySequence.StandardKey.Open, self._file_ctrl.open)
        self._act_save = self._action("&Save", QKeySequence.StandardKey.Save, self._save)
        self._act_save_as = self._action(
            "Save &As…", QKeySequence.StandardKey.SaveAs, self._save_as
        )
        self._file_menu.addAction(self._act_new)
        self._file_menu.addAction(self._act_open)
        self._recent_menu = self._file_menu.addMenu("Open &Recent")
        self._file_menu.addSeparator()
        self._file_menu.addAction(self._act_save)
        self._file_menu.addAction(self._act_save_as)
        self._file_menu.addSeparator()
        self._act_quit = self._action("&Quit", QKeySequence.StandardKey.Quit, self.close)
        self._file_menu.addAction(self._act_quit)
        self._update_recent_menu()

        self._edit_menu = bar.addMenu("&Edit")
        self._act_undo = self._action("&Undo", QKeySequence.StandardKey.Undo, self._undo)
        self._act_undo.setEnabled(False)
        self._act_redo = self._action("Re&do", QKeySequence.StandardKey.Redo, self._redo)
        self._act_redo.setEnabled(False)
        self._edit_menu.addAction(self._act_undo)
        self._edit_menu.addAction(self._act_redo)

        self._project_menu = bar.addMenu("&Project")
        self._act_export = self._action("&Export to Rekordbox…", None, self._export)
        self._act_import = self._action(
            "&Import from Streaming Service…", None, self._import_ctrl.import_streaming
        )
        self._act_settings = self._action("&Service Settings…", None, self._open_settings)
        self._act_themes = self._action("Section Name &Themes…", None, self._open_theme_dialog)
        self._act_rematch_xml = self._action(
            "Re-match with Rekordbox &XML…", None, self._rematch_ctrl.rematch_xml
        )
        self._act_rematch_db = self._action(
            "Re-match with Rekordbox &DB", None, self._rematch_ctrl.rematch_db
        )
        self._act_rematch_manual = self._action(
            "&Manual Re-match…", None, self._rematch_ctrl.rematch_manual
        )
        self._act_update_spotify = self._action(
            "&Update Spotify Playlist Order", None, self._spotify_sync_ctrl.update_order
        )
        self._project_menu.addAction(self._act_export)
        self._project_menu.addSeparator()
        self._project_menu.addAction(self._act_import)
        self._project_menu.addAction(self._act_settings)
        self._project_menu.addSeparator()
        rematch_menu = self._project_menu.addMenu("Re-match with &Rekordbox")
        rematch_menu.addAction(self._act_rematch_xml)
        rematch_menu.addAction(self._act_rematch_db)
        rematch_menu.addSeparator()
        rematch_menu.addAction(self._act_rematch_manual)
        self._act_recolor = self._action("&Recolor sections by type…", None, self._recolor_by_type)
        self._project_menu.addSeparator()
        self._project_menu.addAction(self._act_update_spotify)
        self._project_menu.addSeparator()
        self._project_menu.addAction(self._act_themes)
        self._project_menu.addAction(self._act_recolor)

        # ── Global keyboard shortcuts (not shown in any menu) ─────────────
        for shortcut, slot in [
            (QKeySequence("Ctrl+T"), self._add_track_shortcut),
            (QKeySequence("Ctrl+Shift+S"), self._multi_section_view.add_section_interactive),
            (QKeySequence("Ctrl+F"), self._multi_section_view.focus_filter),
        ]:
            act = QAction(self)
            act.setShortcut(shortcut)
            act.triggered.connect(slot)
            self.addAction(act)

    def _action(self, label: str, shortcut, slot) -> QAction:
        act = QAction(label, self)
        if shortcut is not None:
            act.setShortcut(shortcut)
        act.triggered.connect(slot)
        return act

    # --------------------------------------------------------------- statusbar

    def _setup_statusbar(self) -> None:
        self._status_tracks = QLabel()
        self._status_duration = QLabel()
        self._status_unmatched = QLabel()
        sb = self.statusBar()
        sb.addWidget(self._status_tracks)
        sb.addWidget(QLabel("  |  "))
        sb.addWidget(self._status_duration)
        sb.addWidget(QLabel("  |  "))
        sb.addWidget(self._status_unmatched)

    def _update_statusbar(self) -> None:
        if self._project is None:
            self._status_tracks.setText("No project")
            self._status_duration.setText("")
            self._status_unmatched.setText("")
            return
        all_ids: set = set()
        total_dur = 0
        unmatched = 0
        for sec in self._project.sections:
            for tid in sec.track_ids:
                all_ids.add(tid)
                track = self._project.get_track(tid)
                if track:
                    total_dur += track.duration or 0
                    if track.match_status == MatchStatus.UNMATCHED:
                        unmatched += 1
        mins, secs = divmod(total_dur, 60)
        hours, mins = divmod(mins, 60)
        dur_str = f"{hours}:{mins:02d}:{secs:02d}" if hours else f"{mins}:{secs:02d}"
        self._status_tracks.setText(f"Tracks: {len(all_ids)}")
        self._status_duration.setText(f"Duration: {dur_str}")
        self._status_unmatched.setText(f"Unmatched: {unmatched}")

    # ------------------------------------------------------------ wiring

    # -------------------------------------------------------------- properties

    @property
    def _project(self) -> Project | None:
        return self._ctrl.project

    @property
    def _save_path(self) -> Path | None:
        return self._ctrl.save_path

    @property
    def _dirty(self) -> bool:
        return self._ctrl.dirty

    # ------------------------------------------------------------ wiring

    def _connect_signals(self) -> None:
        self._ctrl.project_changed.connect(self._on_project_changed)
        self._edit_ctrl.project_changed.connect(self._update_undo_actions)
        self._edit_ctrl.project_changed.connect(self._mark_dirty)
        self._multi_section_view.section_modified.connect(self._update_statusbar)
        self._multi_section_view.project_changed.connect(self._update_statusbar)
        self._multi_section_view.add_track_requested.connect(self._add_track)
        self._multi_section_view.track_selected.connect(self._on_track_selected)
        self._multi_section_view.fix_match_requested.connect(self._on_fix_match)
        self._multi_section_view.import_requested.connect(self._import_ctrl.import_streaming)

        self._transition_note.note_edit.connect(self._on_note_edit)
        self._transition_note.about_to_modify.connect(self._push_undo_state)

        # File controller
        self._file_ctrl.recent_changed.connect(self._update_recent_menu)
        self._file_ctrl.note_cleared.connect(lambda: self._transition_note.set_track(None, None))
        self._file_ctrl.undo_cleared.connect(self._clear_undo_history)

        # Import / rematch controllers now talk to EditController directly;
        # no undo_requested / dirty_requested signals to wire here.

    def _on_project_changed(self, project: Project | None) -> None:
        if project is not None and self._save_path is not None and not self._dirty:
            self._check_autosave_recovery(project)
        self._multi_section_view.set_project(project)
        self._update_statusbar()
        self._refresh_title()

    def _on_note_edit(self, section_id, track_id, text: str) -> None:
        self._edit_ctrl.set_transition_note(section_id, track_id, text)

    def _on_track_selected(self, track_id, section: Section | None) -> None:
        self._transition_note.set_track(track_id, section)
        track = self._project.get_track(track_id) if (self._project and track_id) else None
        self._match_info.set_track(track)

    def _on_fix_match(self, track_id, section) -> None:
        if self._project is None:
            return
        track = self._project.get_track(track_id)
        if track is None:
            return
        dialog = FixMatchDialog(track, self)
        if dialog.exec() != FixMatchDialog.DialogCode.Accepted:
            return
        local = dialog.matched_local_track()
        if local is None:
            return
        self._edit_ctrl.apply_match(track_id, local)
        self._multi_section_view.refresh_tracks(self._project.tracks)
        self._on_track_selected(track_id, section)

    # ---------------------------------------------------------------- dirty state

    def _do_autosave(self) -> None:
        if self._project is not None and self._dirty:
            write_autosave(self._project)

    def _check_autosave_recovery(self, project: Project) -> None:
        if self._save_path is None:
            return
        try:
            save_mtime = self._save_path.stat().st_mtime
        except OSError:
            return
        if autosave_mtime(project.id) <= save_mtime:
            return
        recovered = read_autosave(project.id)
        if recovered is None:
            return
        ans = QMessageBox.question(
            self,
            "Recover unsaved changes?",
            f'An autosave of "{project.name}" exists that is newer than the saved file.\n'
            "Restore unsaved changes?",
        )
        if ans == QMessageBox.StandardButton.Yes:
            self._ctrl.restore(recovered)
        else:
            clear_autosave(project.id)

    def _save(self) -> None:
        self._file_ctrl.save()
        if self._project is not None and not self._dirty:
            clear_autosave(self._project.id)

    def _save_as(self) -> None:
        self._file_ctrl.save_as()
        if self._project is not None and not self._dirty:
            clear_autosave(self._project.id)

    def _mark_dirty(self) -> None:
        self._ctrl.mark_dirty()
        self._update_statusbar()
        self._refresh_title()

    def _refresh_title(self) -> None:
        name = self._project.name if self._project else _DEFAULT_PROJECT_NAME
        dirty = " •" if self._dirty else ""
        self.setWindowTitle(f"{name}{dirty} — {_APP_NAME}")

    # ----------------------------------------------------------------- undo / redo

    def _push_undo_state(self) -> None:
        self._edit_ctrl.push_snapshot()
        self._update_undo_actions()

    def _undo(self) -> None:
        self._edit_ctrl.undo()

    def _redo(self) -> None:
        self._edit_ctrl.redo()

    def _update_undo_actions(self) -> None:
        self._act_undo.setEnabled(self._edit_ctrl.can_undo)
        self._act_redo.setEnabled(self._edit_ctrl.can_redo)

    def _clear_undo_history(self) -> None:
        self._edit_ctrl.clear()
        self._update_undo_actions()

    # ---------------------------------------------------------------- file ops

    def _update_recent_menu(self) -> None:
        """Rebuild the Open Recent submenu from persisted recent files."""
        self._recent_menu.clear()
        recent = get_recent_files()
        if not recent:
            act = QAction("(No recent files)", self)
            act.setEnabled(False)
            self._recent_menu.addAction(act)
            return
        for path_str in recent:
            name = Path(path_str).name
            act = QAction(f"{name}  —  {path_str}", self)
            act.setToolTip(path_str)
            act.triggered.connect(lambda checked=False, p=path_str: self._file_ctrl.open_recent(p))
            self._recent_menu.addAction(act)

    # ----------------------------------------------------------------- actions

    def _add_track_shortcut(self) -> None:
        """Ctrl+T — add track to the currently active section."""
        self._add_track()

    def _add_track(self, section: Section | None = None) -> None:
        if self._project is None:
            return
        target = section or self._multi_section_view.current_section()
        if target is None:
            QMessageBox.information(self, "No section", "Add a section before adding a track.")
            return
        dialog = AddTrackDialog(self)
        if dialog.exec() != AddTrackDialog.DialogCode.Accepted:
            return
        track = dialog.track()
        self._edit_ctrl.add_track(track, target.id)
        self._multi_section_view.refresh_section(target)
        self._update_statusbar()

    def _export(self) -> None:
        if self._project is None or not self._project.sections:
            QMessageBox.information(self, "Export", "Add sections before exporting.")
            return
        ExportDialog(self._project, self).exec()

    def _open_theme_dialog(self) -> None:
        if self._project is None:
            return
        snap_before = self._project.model_dump_json()
        ThemeDialog(self._project, self).exec()
        snap_after = self._project.model_dump_json()
        if snap_after != snap_before:
            self._edit_ctrl._stack.push(snap_before)  # noqa: SLF001
            self._update_undo_actions()
            self._mark_dirty()

    def _open_settings(self) -> None:
        SettingsDialog(self).exec()

    def _recolor_by_type(self) -> None:
        changed = self._edit_ctrl.recolor_all_by_type()
        if changed:
            QMessageBox.information(
                self,
                "Sections recolored",
                f"{changed} section(s) recolored to their type's default color.",
            )
        else:
            QMessageBox.information(
                self,
                "No changes",
                "All sections already use their type's default color.",
            )

    # -------------------------------------------------------- window close

    def closeEvent(self, event) -> None:
        """Confirm unsaved changes before closing the window."""
        if self._file_ctrl.confirm_discard():
            event.accept()
        else:
            event.ignore()
