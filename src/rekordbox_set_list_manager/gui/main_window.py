"""Main application window."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QMainWindow, QSplitter, QTabWidget

if TYPE_CHECKING:
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QMenu

from rekordbox_set_list_manager.controllers.autosave_controller import AutosaveController
from rekordbox_set_list_manager.controllers.edit_controller import EditController
from rekordbox_set_list_manager.controllers.file_controller import FileController
from rekordbox_set_list_manager.controllers.import_controller import ImportController
from rekordbox_set_list_manager.controllers.project_actions_controller import (
    ProjectActionsController,
)
from rekordbox_set_list_manager.controllers.project_controller import ProjectController
from rekordbox_set_list_manager.controllers.rematch_controller import RematchController
from rekordbox_set_list_manager.controllers.spotify_sync_controller import SpotifySyncController
from rekordbox_set_list_manager.controllers.track_controller import TrackController
from rekordbox_set_list_manager.gui import menu_bar
from rekordbox_set_list_manager.gui.widgets.panels.match_info_widget import MatchInfoWidget
from rekordbox_set_list_manager.gui.widgets.panels.project_status_bar import ProjectStatusBar
from rekordbox_set_list_manager.gui.widgets.panels.transition_note import TransitionNoteWidget
from rekordbox_set_list_manager.gui.widgets.sections.multi_section_view import MultiSectionView
from rekordbox_set_list_manager.models.project import Project
from rekordbox_set_list_manager.models.section import Section
from rekordbox_set_list_manager.services.spotify_service import SpotifyService

_APP_NAME = "Rekordbox Set List Manager"
_DEFAULT_PROJECT_NAME = "Untitled"


class MainWindow(QMainWindow):
    """Top-level window: composes controllers and views, wires signals."""

    # Menu/action attributes assigned by menu_bar.build_menus().
    _file_menu: QMenu
    _recent_menu: QMenu
    _edit_menu: QMenu
    _project_menu: QMenu
    _act_new: QAction
    _act_open: QAction
    _act_save: QAction
    _act_save_as: QAction
    _act_quit: QAction
    _act_undo: QAction
    _act_redo: QAction
    _act_add_track: QAction
    _act_add_section: QAction
    _act_filter: QAction
    _act_rename: QAction
    _act_export: QAction
    _act_import: QAction
    _act_settings: QAction
    _act_themes: QAction
    _act_rematch_xml: QAction
    _act_rematch_db: QAction
    _act_rematch_manual: QAction
    _act_update_spotify: QAction
    _act_recolor: QAction

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
        self._file_ctrl = FileController(self._ctrl, self._edit_ctrl, self, self)
        self._import_ctrl = ImportController(
            self._ctrl, self._edit_ctrl, self._multi_section_view, self, self
        )
        self._rematch_ctrl = RematchController(
            self._ctrl, self._edit_ctrl, self._multi_section_view, self, self
        )
        self._spotify_sync_ctrl = SpotifySyncController(self._ctrl, self._spotify, self, self)
        self._track_ctrl = TrackController(
            self._ctrl, self._edit_ctrl, self._multi_section_view, self, self
        )
        self._project_actions = ProjectActionsController(
            self._ctrl, self._edit_ctrl, self._multi_section_view, self, self
        )
        # Self-connects to ctrl.project_changed; built before
        # _connect_signals so the recovery check runs ahead of the view
        # refresh on every project_changed emission.
        self._autosave_ctrl = AutosaveController(self._ctrl, self, self)

        menu_bar.build_menus(self)

        self._status_bar = ProjectStatusBar()
        self.statusBar().addWidget(self._status_bar)

        self._connect_signals()

        self.setWindowTitle(_APP_NAME)
        self.setMinimumSize(QSize(700, 500))
        self.resize(1000, 700)

        self._file_ctrl.new_project()

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

    @property
    def _status_tracks(self):
        return self._status_bar._status_tracks  # noqa: SLF001

    @property
    def _status_duration(self):
        return self._status_bar._status_duration  # noqa: SLF001

    @property
    def _status_unmatched(self):
        return self._status_bar._status_unmatched  # noqa: SLF001

    # ------------------------------------------------------------ wiring

    def _connect_signals(self) -> None:
        self._ctrl.project_changed.connect(self._on_project_changed)
        self._ctrl.dirty_changed.connect(self._refresh_title)
        self._edit_ctrl.project_changed.connect(self._update_undo_actions)
        self._edit_ctrl.project_changed.connect(self._refresh_after_edit)
        self._multi_section_view.section_modified.connect(self._update_statusbar)
        self._multi_section_view.project_changed.connect(self._update_statusbar)
        self._multi_section_view.add_track_requested.connect(self._track_ctrl.add_track)
        self._multi_section_view.add_from_rekordbox_requested.connect(
            self._track_ctrl.add_track_from_rekordbox
        )
        self._multi_section_view.track_selected.connect(self._on_track_selected)
        self._multi_section_view.fix_match_requested.connect(self._track_ctrl.fix_match)
        self._track_ctrl.match_fixed.connect(self._on_track_selected)
        self._multi_section_view.import_requested.connect(self._import_ctrl.import_streaming)

        self._transition_note.note_edit.connect(self._on_note_edit)

        # File controller
        self._file_ctrl.recent_changed.connect(self._update_recent_menu)
        self._file_ctrl.note_cleared.connect(lambda: self._transition_note.set_track(None, None))
        self._file_ctrl.undo_cleared.connect(self._clear_undo_history)

        # Import / rematch controllers now talk to EditController directly;
        # no undo_requested / dirty_requested signals to wire here.

    def _on_project_changed(self, project: Project | None) -> None:
        # The autosave recovery slot is connected ahead of this one and may
        # reentrantly restore() a recovered project — a stale outer emission
        # must not repaint the view from the superseded project object.
        if project is not self._ctrl.project:
            return
        self._multi_section_view.set_project(project)
        self._update_statusbar()
        self._refresh_title()

    def _on_note_edit(self, section_id, track_id, text: str) -> None:
        self._edit_ctrl.set_transition_note(section_id, track_id, text)

    def _on_track_selected(self, track_id, section: Section | None) -> None:
        self._transition_note.set_track(track_id, section)
        track = self._project.get_track(track_id) if (self._project and track_id) else None
        self._match_info.set_track(track)

    # ---------------------------------------------------------------- dirty state

    def _save(self) -> None:
        self._file_ctrl.save()

    def _save_as(self) -> None:
        self._file_ctrl.save_as()

    def _refresh_after_edit(self) -> None:
        # Dirty marking already happened in EditController._emit (or via
        # ProjectController.restore on undo/redo); this slot only repaints.
        self._update_statusbar()
        self._refresh_title()

    def _refresh_title(self) -> None:
        name = self._project.name if self._project else _DEFAULT_PROJECT_NAME
        dirty = " •" if self._dirty else ""
        self.setWindowTitle(f"{name}{dirty} — {_APP_NAME}")

    # ----------------------------------------------------------------- undo / redo

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
        menu_bar.update_recent_menu(self)

    # ----------------------------------------------------------------- actions

    def _update_statusbar(self) -> None:
        self._status_bar.update_for(self._project)

    def _export(self) -> None:
        self._project_actions.export()

    def _open_theme_dialog(self) -> None:
        self._project_actions.open_theme_dialog()

    # -------------------------------------------------------- window close

    def closeEvent(self, event) -> None:
        """Confirm unsaved changes before closing the window."""
        if self._file_ctrl.confirm_discard():
            event.accept()
        else:
            event.ignore()
