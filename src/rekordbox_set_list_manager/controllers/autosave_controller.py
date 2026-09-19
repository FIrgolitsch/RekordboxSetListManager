"""Autosave controller: periodic writes and crash-recovery prompt."""

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QMessageBox, QWidget

from rekordbox_set_list_manager.services.autosave import (
    autosave_mtime,
    clear_autosave,
    read_autosave,
    write_autosave,
)

if TYPE_CHECKING:
    from rekordbox_set_list_manager.controllers.project_controller import ProjectController
    from rekordbox_set_list_manager.models.project import Project


class AutosaveController(QObject):
    """Owns the autosave timer and the newer-autosave recovery prompt.

    Self-connects to ``ctrl.project_changed`` so the recovery check runs
    before the view refreshes — provided the controller is constructed
    before the window wires its own ``project_changed`` slots.
    """

    def __init__(
        self,
        ctrl: ProjectController,
        parent_widget: QWidget,
        parent: QObject | None = None,
    ) -> None:
        """Initialise the autosave controller and start the 30s timer."""
        super().__init__(parent)
        self._ctrl = ctrl
        self._w = parent_widget

        self._timer = QTimer(self)
        self._timer.setInterval(30_000)
        self._timer.timeout.connect(self.do_autosave)
        self._timer.start()

        self._ctrl.project_changed.connect(self._check_autosave_recovery)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def do_autosave(self) -> None:
        """Write the current project to its autosave slot when dirty."""
        project = self._ctrl.project
        if project is not None and self._ctrl.dirty:
            write_autosave(project)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _check_autosave_recovery(self, project: Project | None) -> None:
        if project is None or self._ctrl.save_path is None or self._ctrl.dirty:
            return
        try:
            save_mtime = self._ctrl.save_path.stat().st_mtime
        except OSError:
            return
        if autosave_mtime(project.id) <= save_mtime:
            return
        recovered = read_autosave(project.id)
        if recovered is None:
            return
        ans = QMessageBox.question(
            self._w,
            "Recover unsaved changes?",
            f'An autosave of "{project.name}" exists that is newer than the saved file.\n'
            "Restore unsaved changes?",
        )
        if ans == QMessageBox.StandardButton.Yes:
            self._ctrl.restore(recovered)
        else:
            clear_autosave(project.id)
