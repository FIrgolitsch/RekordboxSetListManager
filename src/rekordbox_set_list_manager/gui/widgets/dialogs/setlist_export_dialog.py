"""Dialog for exporting the set list to timestamped TXT, CSV, and M3U8 files."""

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from rekordbox_set_list_manager.models.project import Project
from rekordbox_set_list_manager.services.setlist_export import (
    SetlistExportError,
    export_setlist,
)


class SetlistExportDialog(QDialog):
    """Writes the set list to TXT/CSV/M3U8 files inside one chosen folder."""

    def __init__(self, project: Project, parent=None) -> None:
        """Initialise the setlist export dialog for *project*."""
        super().__init__(parent)
        self.setWindowTitle("Export Setlist Files")
        self.setMinimumWidth(460)
        self._project = project

        self._cb_txt = QCheckBox("TXT")
        self._cb_txt.setChecked(True)
        self._cb_csv = QCheckBox("CSV")
        self._cb_csv.setChecked(True)
        self._cb_m3u8 = QCheckBox("M3U8")
        self._cb_m3u8.setChecked(True)

        formats_row = QHBoxLayout()
        formats_row.addWidget(QLabel("Formats:"))
        formats_row.addWidget(self._cb_txt)
        formats_row.addWidget(self._cb_csv)
        formats_row.addWidget(self._cb_m3u8)
        formats_row.addStretch(1)

        self._cb_uris = QCheckBox("Include streaming URIs for unmatched tracks")
        self._cb_uris.setChecked(True)

        self._dir_edit = QLineEdit()
        self._dir_edit.setReadOnly(True)
        self._dir_edit.setPlaceholderText("Choose output folder…")
        self._browse_btn = QPushButton("Choose Folder…")
        self._browse_btn.clicked.connect(self._browse)

        dir_row = QHBoxLayout()
        dir_row.addWidget(self._dir_edit, 1)
        dir_row.addWidget(self._browse_btn)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Export")
        buttons.accepted.connect(self._on_export)
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addLayout(formats_row)
        root.addWidget(self._cb_uris)
        root.addLayout(dir_row)
        root.addWidget(buttons)

    # ------------------------------------------------------------------ slots

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose export folder")
        if path:
            self._dir_edit.setText(path)

    def _on_export(self) -> None:
        formats = {
            fmt
            for fmt, cb in (
                ("txt", self._cb_txt),
                ("csv", self._cb_csv),
                ("m3u8", self._cb_m3u8),
            )
            if cb.isChecked()
        }
        if not formats:
            QMessageBox.information(self, "Export", "Select at least one format.")
            return
        out_dir = self._dir_edit.text().strip()
        if not out_dir:
            QMessageBox.information(self, "Export", "Please choose an output folder.")
            return

        try:
            result = export_setlist(
                self._project,
                Path(out_dir),
                formats,
                include_streaming_uris=self._cb_uris.isChecked(),
            )
        except SetlistExportError as exc:
            QMessageBox.warning(self, "Export failed", str(exc))
            return

        msg = "Exported:\n" + "\n".join(p.name for p in result.written)
        if not result.skipped:
            QMessageBox.information(self, "Export complete", msg)
        else:
            msg += f"\n{len(result.skipped)} track(s) skipped — see details."
            msg_box = QMessageBox(
                QMessageBox.Icon.Information,
                "Export complete",
                msg,
                QMessageBox.StandardButton.Ok,
                self,
            )
            msg_box.setDetailedText(
                "\n".join(f"{item.label} — {item.reason}" for item in result.skipped)
            )
            msg_box.exec()
        self.accept()
