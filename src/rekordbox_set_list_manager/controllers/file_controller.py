"""File operations controller: new, open, save, save-as, open recent."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget

from rekordbox_set_list_manager.services import telemetry
from rekordbox_set_list_manager.services.project_io import ProjectIOError
from rekordbox_set_list_manager.utils.config import add_recent_file
from rekordbox_set_list_manager.utils.constants import PROJECT_FILE_EXTENSION

if TYPE_CHECKING:
    from rekordbox_set_list_manager.controllers.project_controller import ProjectController

_DEFAULT_PROJECT_NAME = "Untitled"


class FileController(QObject):
    """Handles file operations: new, open, save, save-as, open-recent.

    Emits signals that MainWindow uses to trigger purely UI side effects
    (clear undo history, reset transition note, rebuild recent-file menu).
    """

    recent_changed = Signal()   # recent-files list changed → rebuild menu
    note_cleared = Signal()     # project loaded/created → clear transition note
    undo_cleared = Signal()     # project loaded/created → clear undo history

    def __init__(
        self,
        ctrl: ProjectController,
        parent_widget: QWidget,
        parent: QObject | None = None,
    ) -> None:
        """Initialise the file controller backed by *ctrl*."""
        super().__init__(parent)
        self._ctrl = ctrl
        self._w = parent_widget

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def confirm_discard(self) -> bool:
        """Return True if it is safe to proceed (saved or discarded), False to cancel."""
        if not self._ctrl.dirty:
            return True
        msg = QMessageBox(self._w)
        msg.setWindowTitle("Unsaved changes")
        msg.setText("You have unsaved changes.")
        msg.setIcon(QMessageBox.Icon.Warning)
        save_btn = msg.addButton("Save", QMessageBox.ButtonRole.AcceptRole)
        msg.addButton("Discard", QMessageBox.ButtonRole.DestructiveRole)
        msg.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        msg.setDefaultButton(save_btn)
        msg.exec()
        clicked = msg.clickedButton()
        if clicked is save_btn:
            self.save()
            # If save was cancelled (e.g. Save As dialog dismissed), still dirty → abort
            return not self._ctrl.dirty
        return (
            clicked is not None
            and msg.buttonRole(clicked) == QMessageBox.ButtonRole.DestructiveRole
        )

    def new_project(self) -> None:
        """Create a new empty project, prompting to discard unsaved changes."""
        if not self.confirm_discard():
            return
        self._ctrl.new()
        self.note_cleared.emit()
        self.undo_cleared.emit()

    def open(self) -> None:
        """Prompt the user to select and open a project file."""
        if not self.confirm_discard():
            return
        path_str, _ = QFileDialog.getOpenFileName(
            self._w,
            "Open Project",
            "",
            f"Rekordbox Set List Manager files (*{PROJECT_FILE_EXTENSION})",
        )
        if not path_str:
            return
        self._load(Path(path_str))

    def open_recent(self, path_str: str) -> None:
        """Open a recently-used project by file path string."""
        path = Path(path_str)
        if not path.exists():
            QMessageBox.warning(
                self._w,
                "File not found",
                f"{path_str}\n\nThe file no longer exists.",
            )
            add_recent_file(path_str)  # keep in list (existing behaviour)
            self.recent_changed.emit()
            return
        if not self.confirm_discard():
            return
        self._load(path)

    def save(self) -> None:
        """Save the current project, prompting for a path if not yet set."""
        if self._ctrl.save_path is None:
            self.save_as()
        else:
            self._do_save(self._ctrl.save_path)

    def save_as(self) -> None:
        """Prompt the user for a new file path and save the current project."""
        project = self._ctrl.project
        default = project.name if project else _DEFAULT_PROJECT_NAME
        path_str, _ = QFileDialog.getSaveFileName(
            self._w,
            "Save Project",
            default,
            f"Rekordbox Set List Manager files (*{PROJECT_FILE_EXTENSION})",
        )
        if not path_str:
            return
        path = Path(path_str)
        if path.suffix.lower() != PROJECT_FILE_EXTENSION:
            path = path.with_suffix(PROJECT_FILE_EXTENSION)
        self._do_save(path)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load(self, path: Path) -> None:
        try:
            self._ctrl.load(path)
        except ProjectIOError as exc:
            QMessageBox.critical(self._w, "Open failed", str(exc))
            return
        telemetry.record("project_open")
        self.note_cleared.emit()
        self.undo_cleared.emit()
        self.recent_changed.emit()

    def _do_save(self, path: Path) -> None:
        try:
            self._ctrl.save(path)
        except ProjectIOError as exc:
            QMessageBox.critical(self._w, "Save failed", str(exc))
            return
        telemetry.record("project_save")
        self.undo_cleared.emit()
        self.recent_changed.emit()
