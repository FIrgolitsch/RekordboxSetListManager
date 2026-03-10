"""Dialog for exporting a set list to Rekordbox XML."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from set_manager.models.project import Project
from set_manager.services.rekordbox_xml import RekordboxXmlError, RekordboxXmlService


class ExportDialog(QDialog):
    """Lets the user choose a set list and output path then triggers the export."""

    def __init__(self, project: Project, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export to Rekordbox XML")
        self.setMinimumWidth(420)
        self._project = project
        self._service = RekordboxXmlService()

        # Set list selector
        self._set_list_combo = QComboBox()
        for sl in project.set_lists:
            self._set_list_combo.addItem(sl.name, sl.id)

        # Output path row
        self._path_edit = QLineEdit()
        self._path_edit.setReadOnly(True)
        self._path_edit.setPlaceholderText("Choose output file…")
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse)

        path_row = QHBoxLayout()
        path_row.addWidget(self._path_edit, 1)
        path_row.addWidget(browse_btn)

        form = QFormLayout()
        form.addRow("Set List", self._set_list_combo)
        form.addRow("Output file", path_row)

        self._status = QLabel()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Export")
        buttons.accepted.connect(self._on_export)
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addWidget(self._status)
        root.addWidget(buttons)

    # ------------------------------------------------------------------ slots

    def _browse(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Rekordbox XML", "", "XML files (*.xml)"
        )
        if path:
            if not path.endswith(".xml"):
                path += ".xml"
            self._path_edit.setText(path)

    def _on_export(self) -> None:
        sl_id: UUID = self._set_list_combo.currentData()
        sl = self._project.get_set_list(sl_id)
        if sl is None:
            QMessageBox.warning(self, "Export", "No set list selected.")
            return

        path_str = self._path_edit.text().strip()
        if not path_str:
            QMessageBox.warning(self, "Export", "Please choose an output file.")
            return

        try:
            self._service.export_set(sl, self._project.tracks, Path(path_str))
        except RekordboxXmlError as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return

        QMessageBox.information(
            self, "Export complete", f"Exported to:\n{path_str}"
        )
        self.accept()
