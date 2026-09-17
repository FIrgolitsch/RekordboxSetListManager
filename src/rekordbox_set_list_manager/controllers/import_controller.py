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
    from rekordbox_set_list_manager.gui.widgets.dialogs.import_dialog import ImportDialog
    from rekordbox_set_list_manager.gui.widgets.sections.multi_section_view import MultiSectionView
    from rekordbox_set_list_manager.models.project import Project
    from rekordbox_set_list_manager.models.track import Track


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

        if dialog.sync_mode:
            from rekordbox_set_list_manager.gui.widgets.dialogs.import_dialog import (  # noqa: PLC0415
                _existing_streaming_ids,
                _is_known,
            )

            known = _existing_streaming_ids(project)
            new_tracks = [t for t in tracks if not _is_known(t, known)]
        else:
            new_tracks = list(tracks)

        if not new_tracks:
            QMessageBox.information(self._w, "Already up to date", "No new tracks to import.")
            return

        telemetry.record("import_started", track_count=len(tracks))
        self._commit_import(new_tracks, dialog)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _commit_import(self, new_tracks: list[Track], dialog: ImportDialog) -> None:
        """Funnel the whole import batch through one audited mutation.

        Parameters
        ----------
        new_tracks : list[Track]
            Tracks confirmed by the dialog to add to the project.
        dialog : ImportDialog
            The accepted import dialog; supplies sync mode and playlist IDs.

        """
        target = self._view.current_section()
        section_label = "New tracks" if dialog.sync_mode else "Imported"

        def _apply(proj: Project) -> Section:
            for track in new_tracks:
                proj.add_track(track)
            # Playlist-ID sync must run ahead of both return paths below.
            if dialog.selected_spotify_playlist_id:
                proj.spotify_playlist_id = dialog.selected_spotify_playlist_id
            if dialog.selected_tidal_playlist_id:
                proj.tidal_playlist_id = dialog.selected_tidal_playlist_id
            if target is not None:
                for track in new_tracks:
                    target.add_track(track.id)
                return target
            color = proj.default_color_for(SectionType.GENERAL)
            section = Section(name=section_label, section_type=SectionType.GENERAL, color=color)
            for track in new_tracks:
                section.add_track(track.id)
            proj.add_section(section)
            return section

        section = self._edit_ctrl.apply_mutation(_apply)
        if section is None:
            return
        if target is not None:
            self._view.refresh_section(section)
        else:
            self._view.refresh()
            self._view.scroll_to_section(section.id)

        telemetry.record("import_finished", track_count=len(new_tracks))
        verb = "synced" if dialog.sync_mode else "imported"
        QMessageBox.information(self._w, "Import complete", f"{len(new_tracks)} track(s) {verb}.")
