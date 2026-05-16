"""Import-from-streaming-service controller."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QMessageBox, QWidget

from rekordbox_set_list_manager.models.enums import SectionType
from rekordbox_set_list_manager.models.section import Section
from rekordbox_set_list_manager.services import telemetry

if TYPE_CHECKING:
    from rekordbox_set_list_manager.controllers.edit_controller import EditController
    from rekordbox_set_list_manager.controllers.project_controller import ProjectController
    from rekordbox_set_list_manager.gui.widgets.sections.multi_section_view import MultiSectionView


class ImportController(QObject):
    """Handles importing tracks from Spotify / Tidal into the project."""

    def __init__(
        self,
        ctrl: ProjectController,
        edit_ctrl: EditController,
        view: MultiSectionView,
        parent_widget: QWidget,
        parent: QObject | None = None,
    ) -> None:
        """Initialise the import controller with project and UI dependencies."""
        super().__init__(parent)
        self._ctrl = ctrl
        self._edit_ctrl = edit_ctrl
        self._view = view
        self._w = parent_widget

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def import_streaming(self) -> None:
        """Open the streaming import dialog to import a set list."""
        # Defer import to avoid circular dependency (ImportDialog → MainWindow)
        from rekordbox_set_list_manager.gui.widgets.dialogs.import_dialog import (  # noqa: PLC0415
            ImportDialog,
        )

        project = self._ctrl.project
        if project is None:
            QMessageBox.warning(self._w, "No project", "Open or create a project first.")
            return
        dialog = ImportDialog(project, self._w)
        if dialog.exec() != ImportDialog.DialogCode.Accepted:
            return
        tracks = dialog.results()
        if not tracks:
            return

        telemetry.record("import_started", track_count=len(tracks))
        self._edit_ctrl.push_snapshot()
        for track in tracks:
            project.add_track(track)

        target = self._view.current_section()
        if target is not None:
            for track in tracks:
                target.add_track(track.id)
            self._view.refresh_section(target)
        else:
            color = project.default_color_for(SectionType.GENERAL)
            section = Section(name="Imported", section_type=SectionType.GENERAL, color=color)
            for track in tracks:
                section.add_track(track.id)
            project.add_section(section)
            self._view.refresh()
            self._view.scroll_to_section(section.id)

        self._persist_playlist_id(
            target,
            dialog.selected_spotify_playlist_id,
            dialog.selected_tidal_playlist_id,
        )

        self._edit_ctrl.notify_changed()
        telemetry.record("import_finished", track_count=len(tracks))
        QMessageBox.information(self._w, "Import complete", f"{len(tracks)} track(s) imported.")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _persist_playlist_id(
        self,
        _target: Section | None,
        spotify_pl_id: str | None,
        tidal_pl_id: str | None,
    ) -> None:
        proj = self._ctrl.project
        if proj is None:
            return
        if spotify_pl_id:
            proj.spotify_playlist_id = spotify_pl_id
        if tidal_pl_id:
            proj.tidal_playlist_id = tidal_pl_id
