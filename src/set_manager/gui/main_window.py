"""Main application window — three-panel layout."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSplitter,
)

from set_manager.gui.widgets.add_track_dialog import AddTrackDialog
from set_manager.gui.widgets.export_dialog import ExportDialog
from set_manager.gui.widgets.import_dialog import ImportDialog
from set_manager.gui.widgets.section_panel import SectionPanel
from set_manager.gui.widgets.set_overview import SetOverviewWidget
from set_manager.gui.widgets.settings_dialog import SettingsDialog
from set_manager.gui.widgets.theme_dialog import ThemeDialog
from set_manager.gui.widgets.track_table import TrackTable
from set_manager.gui.widgets.transition_note import TransitionNoteWidget
from set_manager.models.project import Project
from set_manager.models.section import Section
from set_manager.models.set_list import SetList
from set_manager.services.audio_features import AudioFeaturesError, AudioFeaturesService
from set_manager.services.project_io import ProjectIOError, load_project, save_project
from set_manager.services.spotify_service import SpotifyService, SpotifyServiceError
from set_manager.utils.constants import PROJECT_FILE_EXTENSION

_APP_NAME = "Set Manager"
_DEFAULT_PROJECT_NAME = "Untitled"
_MAX_UNDO = 50


class MainWindow(QMainWindow):
    """Top-level window housing the three-panel set-building interface."""

    def __init__(self) -> None:
        super().__init__()
        self._project: Project | None = None
        self._save_path: Path | None = None
        self._dirty: bool = False
        # Track current navigation state for post-action refreshes.
        self._current_set_list: SetList | None = None
        self._current_section: Section | None = None
        # Undo/redo: list of project JSON snapshots (before each mutation).
        self._undo_states: list[str] = []
        self._redo_states: list[str] = []

        self._section_panel = SectionPanel()
        self._track_table = TrackTable()
        self._transition_note = TransitionNoteWidget()
        self._set_overview = SetOverviewWidget()

        # Right panel: track table | transition note | energy overview.
        right_splitter = QSplitter(Qt.Orientation.Vertical)
        right_splitter.addWidget(self._track_table)
        right_splitter.addWidget(self._transition_note)
        right_splitter.addWidget(self._set_overview)
        right_splitter.setSizes([448, 100, 152])
        right_splitter.setChildrenCollapsible(False)

        # Outer splitter: section tree | right panel
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._section_panel)
        splitter.addWidget(right_splitter)
        splitter.setSizes([240, 760])
        splitter.setChildrenCollapsible(False)
        self.setCentralWidget(splitter)

        self._setup_menus()
        self._setup_statusbar()
        self._connect_signals()

        self.setWindowTitle(_APP_NAME)
        self.setMinimumSize(QSize(900, 600))
        self.resize(1100, 700)

        # Start with a blank project.
        self._new_project()

    # ------------------------------------------------------------------ menus

    def _setup_menus(self) -> None:
        bar = self.menuBar()

        # File
        self._file_menu = bar.addMenu("&File")
        self._act_new = self._action("&New", QKeySequence.StandardKey.New, self._new_project)
        self._act_open = self._action("&Open…", QKeySequence.StandardKey.Open, self._open)
        self._act_save = self._action("&Save", QKeySequence.StandardKey.Save, self._save)
        self._act_save_as = self._action(
            "Save &As…", QKeySequence.StandardKey.SaveAs, self._save_as
        )
        self._file_menu.addAction(self._act_new)
        self._file_menu.addAction(self._act_open)
        self._file_menu.addSeparator()
        self._file_menu.addAction(self._act_save)
        self._file_menu.addAction(self._act_save_as)
        self._file_menu.addSeparator()
        self._act_quit = self._action("&Quit", QKeySequence.StandardKey.Quit, self.close)
        self._file_menu.addAction(self._act_quit)

        # Edit — undo / redo
        self._edit_menu = bar.addMenu("&Edit")
        self._act_undo = self._action("&Undo", QKeySequence.StandardKey.Undo, self._undo)
        self._act_undo.setEnabled(False)
        self._act_redo = self._action("Re&do", QKeySequence.StandardKey.Redo, self._redo)
        self._act_redo.setEnabled(False)
        self._edit_menu.addAction(self._act_undo)
        self._edit_menu.addAction(self._act_redo)

        # Project
        self._project_menu = bar.addMenu("&Project")
        self._act_export = self._action("&Export to Rekordbox…", None, self._export)
        self._act_import = self._action(
            "&Import from Streaming Service…", None, self._import_streaming
        )
        self._act_settings = self._action("&Service Settings…", None, self._open_settings)
        self._act_themes = self._action(
            "Section Name &Themes…", None, self._open_theme_dialog
        )
        self._act_audio = self._action(
            "&Fetch Audio Features…", None, self._fetch_audio_features
        )
        self._project_menu.addAction(self._act_export)
        self._project_menu.addSeparator()
        self._project_menu.addAction(self._act_import)
        self._project_menu.addAction(self._act_settings)
        self._project_menu.addSeparator()
        self._project_menu.addAction(self._act_themes)
        self._project_menu.addSeparator()
        self._project_menu.addAction(self._act_audio)

    def _action(
        self,
        label: str,
        shortcut: QKeySequence.StandardKey | None,
        slot,
    ) -> QAction:
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

        all_ids = set()
        total_dur = 0
        unmatched = 0
        for sl in self._project.set_lists:
            for sec in sl.sections:
                for tid in sec.track_ids:
                    all_ids.add(tid)
                    track = self._project.get_track(tid)
                    if track:
                        total_dur += track.duration or 0
                        if str(track.match_status) == "unmatched":
                            unmatched += 1

        mins, secs = divmod(total_dur, 60)
        hours, mins = divmod(mins, 60)
        dur_str = f"{hours}:{mins:02d}:{secs:02d}" if hours else f"{mins}:{secs:02d}"

        self._status_tracks.setText(f"Tracks: {len(all_ids)}")
        self._status_duration.setText(f"Duration: {dur_str}")
        self._status_unmatched.setText(f"Unmatched: {unmatched}")

    # ------------------------------------------------------------ wiring

    def _connect_signals(self) -> None:
        self._section_panel.selection_changed.connect(self._on_selection_changed)
        self._section_panel.project_changed.connect(self._mark_dirty)
        self._section_panel.about_to_modify.connect(self._push_undo_state)

        self._track_table.section_modified.connect(self._mark_dirty)
        self._track_table.section_modified.connect(self._update_statusbar)
        self._track_table.add_track_requested.connect(self._add_track)
        self._track_table.track_selected.connect(self._on_track_selected)
        self._track_table.about_to_modify.connect(self._push_undo_state)

        self._transition_note.note_changed.connect(self._mark_dirty)
        self._transition_note.about_to_modify.connect(self._push_undo_state)

    def _on_selection_changed(
        self, set_list: SetList | None, section: Section | None
    ) -> None:
        if self._project is None:
            return
        self._current_set_list = set_list
        self._current_section = section
        self._track_table.set_section(set_list, section, self._project.tracks)
        self._set_overview.set_set_list(set_list, self._project.tracks)
        # Clear the transition note until a track is selected in the new section.
        self._transition_note.set_track(None, None)

    def _on_track_selected(self, track_id) -> None:
        self._transition_note.set_track(track_id, self._current_section)

    # ---------------------------------------------------------------- dirty state

    def _mark_dirty(self) -> None:
        self._dirty = True
        self._update_statusbar()
        self._refresh_title()

    def _refresh_title(self) -> None:
        name = self._project.name if self._project else _DEFAULT_PROJECT_NAME
        dirty = " •" if self._dirty else ""
        self.setWindowTitle(f"{name}{dirty} — {_APP_NAME}")

    # ----------------------------------------------------------------- undo / redo

    def _push_undo_state(self) -> None:
        """Snapshot the current project JSON onto the undo stack.

        Call this *before* any mutation so the state can be restored.
        Clears the redo stack (a new action invalidates the redo history).
        """
        if self._project is None:
            return
        snap = self._project.model_dump_json()
        # Avoid duplicate consecutive snapshots (e.g. a dialog that made no changes).
        if self._undo_states and self._undo_states[-1] == snap:
            return
        self._undo_states.append(snap)
        if len(self._undo_states) > _MAX_UNDO:
            self._undo_states.pop(0)
        self._redo_states.clear()
        self._update_undo_actions()

    def _undo(self) -> None:
        if not self._undo_states or self._project is None:
            return
        self._redo_states.append(self._project.model_dump_json())
        snap = self._undo_states.pop()
        self._restore_snapshot(snap)

    def _redo(self) -> None:
        if not self._redo_states or self._project is None:
            return
        self._undo_states.append(self._project.model_dump_json())
        snap = self._redo_states.pop()
        self._restore_snapshot(snap)

    def _restore_snapshot(self, snap: str) -> None:
        """Deserialise *snap* and fully refresh the UI."""
        self._project = Project.model_validate_json(snap)
        self._dirty = True
        # Clear navigation state; the user can re-click the section they want.
        self._current_set_list = None
        self._current_section = None
        self._transition_note.set_track(None, None)
        self._section_panel.set_project(self._project)
        self._track_table.set_section(None, None, {})
        self._set_overview.set_set_list(None, {})
        self._update_statusbar()
        self._refresh_title()
        self._update_undo_actions()

    def _update_undo_actions(self) -> None:
        self._act_undo.setEnabled(bool(self._undo_states))
        self._act_redo.setEnabled(bool(self._redo_states))

    def _clear_undo_history(self) -> None:
        self._undo_states.clear()
        self._redo_states.clear()
        self._update_undo_actions()

    # ---------------------------------------------------------------- file ops

    def _new_project(self) -> None:
        if not self._confirm_discard():
            return
        self._project = Project(name=_DEFAULT_PROJECT_NAME)
        self._save_path = None
        self._dirty = False
        self._current_set_list = None
        self._current_section = None
        self._section_panel.set_project(self._project)
        self._track_table.set_section(None, None, {})
        self._set_overview.set_set_list(None, {})
        self._clear_undo_history()
        self._update_statusbar()
        self._refresh_title()

    def _open(self) -> None:
        if not self._confirm_discard():
            return
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            "Open Project",
            "",
            f"Set Manager files (*{PROJECT_FILE_EXTENSION})",
        )
        if not path_str:
            return
        try:
            project = load_project(Path(path_str))
        except ProjectIOError as exc:
            QMessageBox.critical(self, "Open failed", str(exc))
            return
        self._project = project
        self._save_path = Path(path_str)
        self._dirty = False
        self._current_set_list = None
        self._current_section = None
        self._section_panel.set_project(self._project)
        self._track_table.set_section(None, None, self._project.tracks)
        self._set_overview.set_set_list(None, {})
        self._clear_undo_history()
        self._update_statusbar()
        self._refresh_title()

    def _save(self) -> None:
        if self._save_path is None:
            self._save_as()
        else:
            self._do_save(self._save_path)

    def _save_as(self) -> None:
        path_str, _ = QFileDialog.getSaveFileName(
            self,
            "Save Project",
            self._project.name if self._project else _DEFAULT_PROJECT_NAME,
            f"Set Manager files (*{PROJECT_FILE_EXTENSION})",
        )
        if not path_str:
            return
        path = Path(path_str)
        if path.suffix.lower() != PROJECT_FILE_EXTENSION:
            path = path.with_suffix(PROJECT_FILE_EXTENSION)
        self._do_save(path)
        self._save_path = path

    def _do_save(self, path: Path) -> None:
        if self._project is None:
            return
        self._project.touch()
        try:
            save_project(self._project, path)
        except ProjectIOError as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return
        self._dirty = False
        self._refresh_title()

    def _confirm_discard(self) -> bool:
        """Returns True if it is safe to proceed (discard unsaved changes)."""
        if not self._dirty:
            return True
        answer = QMessageBox.question(
            self,
            "Unsaved changes",
            "You have unsaved changes. Discard them?",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Discard

    # ----------------------------------------------------------------- actions

    def _add_track(self) -> None:
        dialog = AddTrackDialog(self)
        if dialog.exec() != AddTrackDialog.DialogCode.Accepted:
            return
        track = dialog.track()
        self._push_undo_state()  # before mutation
        self._project.add_track(track)
        self._track_table.add_track(track)
        self._update_statusbar()

    def _export(self) -> None:
        if self._project is None or not self._project.set_lists:
            QMessageBox.information(self, "Export", "Add a set list before exporting.")
            return
        dialog = ExportDialog(self._project, self)
        dialog.exec()

    def _open_theme_dialog(self) -> None:
        if self._project is None:
            return
        # Capture a snapshot before opening; only push it if the project changed.
        snap_before = self._project.model_dump_json()
        dialog = ThemeDialog(self._project, self)
        dialog.exec()
        snap_after = self._project.model_dump_json()
        if snap_after != snap_before:
            # Theme data changed — register the before-state as an undo step.
            self._undo_states.append(snap_before)
            if len(self._undo_states) > _MAX_UNDO:
                self._undo_states.pop(0)
            self._redo_states.clear()
            self._update_undo_actions()
            self._mark_dirty()

    def _import_streaming(self) -> None:
        if self._project is None:
            QMessageBox.warning(self, "No project", "Open or create a project first.")
            return
        dialog = ImportDialog(self._project, self)
        if dialog.exec() != ImportDialog.DialogCode.Accepted:
            return
        tracks = dialog.results()
        if not tracks:
            return
        self._push_undo_state()  # before mutation
        for track in tracks:
            self._project.add_track(track)
        self._mark_dirty()
        QMessageBox.information(
            self, "Import complete", f"{len(tracks)} track(s) added to project."
        )

    def _open_settings(self) -> None:
        SettingsDialog(self).exec()

    def _fetch_audio_features(self) -> None:
        if self._project is None:
            QMessageBox.warning(self, "No project", "Open or create a project first.")
            return

        spotify_tracks = [t for t in self._project.tracks.values() if t.spotify_id]
        if not spotify_tracks:
            QMessageBox.information(
                self,
                "Fetch Audio Features",
                "No tracks with Spotify IDs found in this project.\n"
                "Import tracks from Spotify first.",
            )
            return

        spotify = SpotifyService()
        try:
            spotify.authenticate()
        except SpotifyServiceError as exc:
            QMessageBox.critical(self, "Spotify Error", str(exc))
            return

        svc = AudioFeaturesService(spotify)
        try:
            updated_tracks = svc.fetch_features(spotify_tracks)
        except AudioFeaturesError as exc:
            QMessageBox.critical(self, "Audio Features Error", str(exc))
            return

        self._push_undo_state()  # before applying features to the project
        for track in updated_tracks:
            self._project.tracks[track.id] = track

        self._mark_dirty()
        self._track_table.set_section(
            self._current_set_list,
            self._current_section,
            self._project.tracks,
        )
        self._set_overview.set_set_list(self._current_set_list, self._project.tracks)

        fetched = sum(1 for t in updated_tracks if t.energy is not None)
        QMessageBox.information(
            self,
            "Audio Features",
            f"Fetched energy/danceability/valence for {fetched} of {len(spotify_tracks)} track(s).",
        )

    # -------------------------------------------------------- window close

    def closeEvent(self, event) -> None:
        if self._confirm_discard():
            event.accept()
        else:
            event.ignore()
