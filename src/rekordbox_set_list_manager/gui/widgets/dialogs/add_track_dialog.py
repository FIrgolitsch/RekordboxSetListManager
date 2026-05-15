"""Dialog for manually adding a track to the project."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QSpinBox,
    QVBoxLayout,
)

from rekordbox_set_list_manager.models.track import Track


class AddTrackDialog(QDialog):
    """Simple form to enter Track metadata manually."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Track")
        self.setMinimumWidth(360)

        self._title = QLineEdit()
        self._artist = QLineEdit()

        self._bpm = QDoubleSpinBox()
        self._bpm.setRange(0, 300)
        self._bpm.setDecimals(1)
        self._bpm.setSpecialValueText("—")  # 0 means "not set"

        self._key = QLineEdit()
        self._key.setMaxLength(8)
        self._key.setPlaceholderText("e.g. Am, C")

        self._duration = QSpinBox()
        self._duration.setRange(0, 7200)  # max 2 hours
        self._duration.setSuffix(" s")
        self._duration.setSpecialValueText("—")

        form = QFormLayout()
        form.addRow("Title *", self._title)
        form.addRow("Artist *", self._artist)
        form.addRow("BPM", self._bpm)
        form.addRow("Key", self._key)
        form.addRow("Duration", self._duration)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.accepted.connect(self._on_accept)
        self._buttons.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addWidget(self._buttons)

    def _on_accept(self) -> None:
        if not self._title.text().strip() or not self._artist.text().strip():
            QMessageBox.warning(self, "Missing fields", "Title and Artist are required.")
            return
        self.accept()

    def track(self) -> Track:
        """Return a new Track built from the form values.

        Only call this after :meth:`exec` returns ``QDialog.DialogCode.Accepted``.
        """
        bpm = self._bpm.value() or None
        duration = self._duration.value() or None
        return Track(
            title=self._title.text().strip(),
            artist=self._artist.text().strip(),
            bpm=bpm,
            key=self._key.text().strip() or None,
            duration=duration,
        )
