"""Project-menu actions controller: rename, export, themes, settings, recolor."""

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QInputDialog, QMessageBox, QWidget

if TYPE_CHECKING:
    from rekordbox_set_list_manager.controllers.edit_controller import EditController
    from rekordbox_set_list_manager.controllers.project_controller import ProjectController
    from rekordbox_set_list_manager.gui.widgets.sections.multi_section_view import (
        MultiSectionView,
    )


class ProjectActionsController(QObject):
    """Handles the Project-menu workflows: rename, export, recolor, themes, settings."""

    def __init__(
        self,
        ctrl: ProjectController,
        edit_ctrl: EditController,
        view: MultiSectionView,
        parent_widget: QWidget,
        parent: QObject | None = None,
    ) -> None:
        """Initialise the project actions controller with project and UI dependencies."""
        super().__init__(parent)
        self._ctrl = ctrl
        self._edit_ctrl = edit_ctrl
        self._view = view
        self._w = parent_widget

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def rename_project(self) -> None:
        """Prompt for a new project name and apply it through the funnel."""
        project = self._ctrl.project
        if project is None:
            return
        name, ok = QInputDialog.getText(
            self._w,
            "Rename Project",
            "Project name:",
            text=project.name,
        )
        if ok and name.strip():
            self._edit_ctrl.rename_project(name.strip())

    def export(self) -> None:
        """Open the Rekordbox export dialog for the current project."""
        from rekordbox_set_list_manager.gui.widgets.dialogs.export_dialog import (  # noqa: PLC0415
            ExportDialog,
        )

        project = self._ctrl.project
        if project is None or not project.sections:
            QMessageBox.information(self._w, "Export", "Add sections before exporting.")
            return
        ExportDialog(project, self._w).exec()

    def open_theme_dialog(self) -> None:
        """Open the section-name themes dialog inside one funneled mutation."""
        from rekordbox_set_list_manager.gui.widgets.dialogs.theme_dialog import (  # noqa: PLC0415
            ThemeDialog,
        )

        if self._ctrl.project is None:
            return
        # The dialog live-mutates project.themes inside exec(); the funnel
        # fingerprints before/after and records one undo step iff it changed.
        self._edit_ctrl.apply_mutation(lambda proj: ThemeDialog(proj, self._w).exec())
        # Repaint unconditionally — the dialog may have renamed sections and
        # a repaint is idempotent.
        self._view.refresh()

    def open_settings(self) -> None:
        """Open the service settings dialog."""
        from rekordbox_set_list_manager.gui.widgets.dialogs.settings_dialog import (  # noqa: PLC0415
            SettingsDialog,
        )

        SettingsDialog(self._w).exec()

    def recolor_by_type(self) -> None:
        """Recolor all sections to their type's default color and report the count."""
        changed = self._edit_ctrl.recolor_all_by_type()
        if changed:
            QMessageBox.information(
                self._w,
                "Sections recolored",
                f"{changed} section(s) recolored to their type's default color.",
            )
        else:
            QMessageBox.information(
                self._w,
                "No changes",
                "All sections already use their type's default color.",
            )
