"""Dialog for picking a single track from the local Rekordbox collection."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
)

from rekordbox_set_list_manager.gui.widgets.streaming.collection_browser import (
    CollectionBrowserWidget,
)
from rekordbox_set_list_manager.services.rekordbox_db import RekordboxDbService

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

    from rekordbox_set_list_manager.models.track import Track


class PickFromRekordboxDialog(QDialog):
    """Browse the local Rekordbox collection and pick one track to add."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add from Rekordbox DB")
        self.resize(760, 480)

        self._browser = CollectionBrowserWidget(self)
        self._browser.track_selected.connect(self._on_selection_changed)
        self._browser.collection_loaded.connect(self._on_collection_loaded)

        self._status = QLabel("Load a collection to browse tracks.")

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._ok_btn = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._ok_btn.setEnabled(False)
        self._ok_btn.setText("Add Track")
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self._browser, 1)
        layout.addWidget(self._status)
        layout.addWidget(self._buttons)

        # Auto-load DB if available so the user doesn't have to click
        db = RekordboxDbService()
        if db.is_available():
            self._browser._load_db()  # noqa: SLF001

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_collection_loaded(self, count: int) -> None:
        self._status.setText(f"{count} track(s) loaded. Select one to add.")

    def _on_selection_changed(self, track: Track | None) -> None:
        self._ok_btn.setEnabled(track is not None)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def track(self) -> Track | None:
        """Return the selected track, or None if the dialog was rejected."""
        return self._browser.selected_track
