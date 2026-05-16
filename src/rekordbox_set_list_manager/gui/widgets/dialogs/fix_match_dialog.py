"""Dialog for manually fixing a track match from the Rekordbox library."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QLabel,
    QVBoxLayout,
)

from rekordbox_set_list_manager.gui.widgets.streaming.collection_browser import (
    CollectionBrowserWidget,
)

if TYPE_CHECKING:
    from rekordbox_set_list_manager.models.track import Track


class FixMatchDialog(QDialog):
    """Browse the Rekordbox library and assign a manual match to a track."""

    def __init__(self, track: Track, parent=None) -> None:
        """Open the fix-match dialog for *track*."""
        super().__init__(parent)
        self.setWindowTitle("Fix Match")
        self.setMinimumSize(750, 520)

        # ── Source track info ──────────────────────────────────────────────
        src_box = QGroupBox("Track to match")
        src_layout = QVBoxLayout(src_box)
        src_layout.addWidget(QLabel(f"<b>{track.title}</b> — {track.artist}"))
        details: list[str] = []
        if track.isrc:
            details.append(f"ISRC: {track.isrc}")
        details.append(f"Status: {track.match_status}")
        src_layout.addWidget(QLabel("  |  ".join(details)))

        # ── Browser ────────────────────────────────────────────────────────
        self._browser = CollectionBrowserWidget()
        self._browser.track_selected.connect(self._on_track_selected)

        # ── Buttons ────────────────────────────────────────────────────────
        self._btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._btn_box.accepted.connect(self.accept)
        self._btn_box.rejected.connect(self.reject)
        self._btn_box.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)

        # ── Root layout ────────────────────────────────────────────────────
        root = QVBoxLayout(self)
        root.addWidget(src_box)
        root.addWidget(self._browser, 1)
        root.addWidget(self._btn_box)

    def matched_local_track(self) -> Track | None:
        """Return the track the user selected as the correct match, or None."""
        return self._browser.selected_track

    # ── private ───────────────────────────────────────────────────────────

    def _on_track_selected(self, track: Track | None) -> None:
        self._btn_box.button(QDialogButtonBox.StandardButton.Ok).setEnabled(
            track is not None
        )
