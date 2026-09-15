"""Dialog for exporting to Rekordbox XML or directly to the Rekordbox database."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
)

from rekordbox_set_list_manager.models.project import Project
from rekordbox_set_list_manager.services.rekordbox_db import RekordboxDbError, RekordboxDbService
from rekordbox_set_list_manager.services.rekordbox_xml import RekordboxXmlError, RekordboxXmlService


class ExportDialog(QDialog):
    """Lets the user export to the auto-detected Rekordbox DB or to an XML file."""

    def __init__(self, project: Project, parent=None) -> None:
        """Initialise the export dialog for *project*."""
        super().__init__(parent)
        self.setWindowTitle("Export to Rekordbox")
        self.setMinimumWidth(460)
        self._project = project
        self._xml_service = RekordboxXmlService()
        self._db_service = RekordboxDbService()
        self._db_available = self._db_service.is_available()

        # --- Mode radio buttons ---
        self._rb_db = QRadioButton("Export directly to Rekordbox database")
        self._rb_xml = QRadioButton("Export to XML file")
        if self._db_available:
            self._rb_db.setChecked(True)
        else:
            self._rb_db.setEnabled(False)
            self._rb_db.setToolTip("No Rekordbox database detected on this machine.")
            self._rb_xml.setChecked(True)

        self._rb_db.toggled.connect(self._on_mode_changed)

        mode_layout = QVBoxLayout()
        mode_layout.addWidget(self._rb_db)
        mode_layout.addWidget(self._rb_xml)

        # --- DB note label ---
        self._db_note = QLabel(
            "<small>Close Rekordbox before exporting to avoid conflicts. "
            "The export replaces any existing folder with the same project name.</small>"
        )
        self._db_note.setWordWrap(True)

        # --- XML path row ---
        self._path_edit = QLineEdit()
        self._path_edit.setReadOnly(True)
        self._path_edit.setPlaceholderText("Choose output file…")
        self._browse_btn = QPushButton("Browse…")
        self._browse_btn.clicked.connect(self._browse)

        path_row = QHBoxLayout()
        path_row.addWidget(self._path_edit, 1)
        path_row.addWidget(self._browse_btn)

        self._path_form = QFormLayout()
        self._path_form.addRow("Output file", path_row)

        self._status = QLabel()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Export")
        buttons.accepted.connect(self._on_export)
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addLayout(mode_layout)
        root.addWidget(self._db_note)
        root.addLayout(self._path_form)
        root.addWidget(self._status)
        root.addWidget(buttons)

        self._on_mode_changed()

    # ------------------------------------------------------------------ slots

    def _on_mode_changed(self) -> None:
        db_mode = self._rb_db.isChecked()
        self._db_note.setVisible(db_mode)
        self._path_edit.setVisible(not db_mode)
        self._browse_btn.setVisible(not db_mode)

    def _browse(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export Rekordbox XML", "", "XML files (*.xml)")
        if path:
            if not path.endswith(".xml"):
                path += ".xml"
            self._path_edit.setText(path)

    def _on_export(self) -> None:
        if self._rb_db.isChecked():
            self._export_to_db()
        else:
            self._export_to_xml()

    def _export_to_db(self) -> None:
        try:
            exported, skipped = self._db_service.export_set_to_db(
                self._project.sections,
                self._project.name,
                self._project.tracks,
            )
        except RekordboxDbError as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return

        msg = f"Exported {exported} track(s) to Rekordbox database."
        if skipped:
            msg += f"\n{len(skipped)} unmatched track(s) were skipped."

        msg_box = QMessageBox(
            QMessageBox.Icon.Information,
            "Export complete",
            msg,
            QMessageBox.StandardButton.Ok,
            self,
        )
        if skipped:
            msg_box.setDetailedText("\n".join(f"{item.label} — {item.reason}" for item in skipped))
        msg_box.exec()
        self.accept()

    def _export_to_xml(self) -> None:
        path_str = self._path_edit.text().strip()
        if not path_str:
            QMessageBox.warning(self, "Export", "Please choose an output file.")
            return

        try:
            self._xml_service.export_set(
                self._project.sections,
                self._project.name,
                self._project.tracks,
                Path(path_str),
            )
        except RekordboxXmlError as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return

        QMessageBox.information(self, "Export complete", f"Exported to:\n{path_str}")
        self.accept()
