"""View-coupled track actions controller: add track, add from Rekordbox, fix match."""

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QMessageBox, QWidget

if TYPE_CHECKING:
    from uuid import UUID

    from rekordbox_set_list_manager.controllers.edit_controller import EditController
    from rekordbox_set_list_manager.controllers.project_controller import ProjectController
    from rekordbox_set_list_manager.gui.widgets.sections.multi_section_view import (
        MultiSectionView,
    )
    from rekordbox_set_list_manager.models.section import Section


class TrackController(QObject):
    """Handles the view-coupled track workflows: add, pick from Rekordbox, fix match.

    ``match_fixed(track_id, section)`` re-emits a completed fix-match so the
    window can keep a single panel-refresh path for selection updates.
    """

    match_fixed = Signal(object, object)  # (track_id, section)

    def __init__(
        self,
        ctrl: ProjectController,
        edit_ctrl: EditController,
        view: MultiSectionView,
        parent_widget: QWidget,
        parent: QObject | None = None,
    ) -> None:
        """Initialise the track controller with project and UI dependencies."""
        super().__init__(parent)
        self._ctrl = ctrl
        self._edit_ctrl = edit_ctrl
        self._view = view
        self._w = parent_widget

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_track(self, section: Section | None = None) -> None:
        """Open the add-track dialog and insert the new track into *section*."""
        # Defer import to avoid circular dependency (dialogs → MainWindow)
        from rekordbox_set_list_manager.gui.widgets.dialogs.add_track_dialog import (  # noqa: PLC0415
            AddTrackDialog,
        )

        if self._ctrl.project is None:
            return
        target = section or self._view.current_section()
        if target is None:
            QMessageBox.information(self._w, "No section", "Add a section before adding a track.")
            return
        dialog = AddTrackDialog(self._w)
        if dialog.exec() != AddTrackDialog.DialogCode.Accepted:
            return
        track = dialog.track()
        self._edit_ctrl.add_track(track, target.id)
        self._view.refresh_section(target)

    def add_track_from_rekordbox(self, section: Section | None = None) -> None:
        """Open the Rekordbox picker and insert the chosen track into *section*."""
        from rekordbox_set_list_manager.gui.widgets.dialogs.pick_from_rekordbox_dialog import (  # noqa: PLC0415
            PickFromRekordboxDialog,
        )

        if self._ctrl.project is None:
            return
        target = section or self._view.current_section()
        if target is None:
            QMessageBox.information(self._w, "No section", "Add a section before adding a track.")
            return
        dialog = PickFromRekordboxDialog(self._w)
        if dialog.exec() != PickFromRekordboxDialog.DialogCode.Accepted:
            return
        track = dialog.track()
        if track is None:
            return
        self._edit_ctrl.add_track(track, target.id)
        self._view.refresh_section(target)

    def fix_match(self, track_id: UUID, section: Section) -> None:
        """Open the fix-match dialog for *track_id* and apply the chosen match."""
        from rekordbox_set_list_manager.gui.widgets.dialogs.fix_match_dialog import (  # noqa: PLC0415
            FixMatchDialog,
        )

        project = self._ctrl.project
        if project is None:
            return
        track = project.get_track(track_id)
        if track is None:
            return
        dialog = FixMatchDialog(track, self._w)
        if dialog.exec() != FixMatchDialog.DialogCode.Accepted:
            return
        local = dialog.matched_local_track()
        if local is None:
            return
        self._edit_ctrl.apply_match(track_id, local)
        self._view.refresh_tracks(project.tracks)
        self.match_fixed.emit(track_id, section)
