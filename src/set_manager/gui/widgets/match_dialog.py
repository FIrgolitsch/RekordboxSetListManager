"""Dialog for reviewing and accepting Spotify import match results."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from set_manager.models.track import Track
from set_manager.services.track_matcher import MatchResult, MatchStrategy

_GREEN = QColor(180, 230, 180, 120)
_YELLOW = QColor(255, 230, 100, 120)

_STRATEGY_LABELS = {
    MatchStrategy.ISRC: "ISRC",
    MatchStrategy.EXACT: "Exact",
    MatchStrategy.FUZZY: "Fuzzy",
    MatchStrategy.FILENAME: "Filename",
    MatchStrategy.NONE: "—",
}

_COLUMNS = ["Title", "Artist", "Status", "Match", "Local File", "Add?"]


class MatchDialog(QDialog):
    """Shows all match results and lets the user choose which unmatched tracks to import.

    Matched tracks are always included.  Unmatched tracks start as skipped;
    tick the checkbox to include them without a local file link.
    """

    def __init__(self, results: list[MatchResult], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Review Import Results")
        self.setMinimumSize(780, 460)
        self._results = results

        matched = sum(1 for r in results if r.strategy != MatchStrategy.NONE)
        unmatched = len(results) - matched

        summary = QLabel(
            f"<b>{matched}</b> track(s) matched to local files  |  "
            f"<b>{unmatched}</b> unmatched (tick to import anyway without local file)"
        )
        summary.setWordWrap(True)

        self._table = QTableWidget(len(results), len(_COLUMNS))
        self._table.setHorizontalHeaderLabels(_COLUMNS)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(
            5, QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setVisible(False)

        self._checkboxes: list[QCheckBox | None] = []
        for row, result in enumerate(results):
            self._populate_row(row, result)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Import selected")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addWidget(summary)
        root.addWidget(self._table)
        root.addWidget(buttons)

    def accepted_tracks(self) -> list[Track]:
        """Return matched tracks + any unmatched tracks the user ticked."""
        tracks: list[Track] = []
        for i, result in enumerate(self._results):
            if result.strategy != MatchStrategy.NONE:
                assert result.local_track is not None
                tracks.append(result.local_track)
            else:
                cb = self._checkboxes[i]
                if cb is not None and cb.isChecked():
                    tracks.append(result.spotify_track)
        return tracks

    # ------------------------------------------------------------------

    def _populate_row(self, row: int, result: MatchResult) -> None:
        sp = result.spotify_track
        is_matched = result.strategy != MatchStrategy.NONE

        score_pct = f"{result.score * 100:.0f}%" if result.score > 0 else ""
        strategy_label = _STRATEGY_LABELS[result.strategy]
        match_str = f"{strategy_label} {score_pct}".strip() if is_matched else "Unmatched"

        local_file = ""
        if result.local_track and result.local_track.filepath:
            local_file = result.local_track.filepath

        row_data = [
            sp.title,
            sp.artist,
            "Matched" if is_matched else "Unmatched",
            match_str,
            local_file,
        ]

        bg = _GREEN if is_matched else _YELLOW
        for col, text in enumerate(row_data):
            item = QTableWidgetItem(text)
            item.setBackground(bg)
            self._table.setItem(row, col, item)

        # "Add?" column — checkbox for unmatched; always-on marker for matched
        if is_matched:
            item = QTableWidgetItem("✓")
            item.setTextAlignment(int(Qt.AlignmentFlag.AlignCenter))
            item.setBackground(bg)
            self._table.setItem(row, 5, item)
            self._checkboxes.append(None)
        else:
            cb = QCheckBox()
            cb.setToolTip("Tick to import this track without a local file link")
            wrapper = QWidget()
            layout = QHBoxLayout(wrapper)
            layout.addWidget(cb)
            layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.setContentsMargins(0, 0, 0, 0)
            self._table.setCellWidget(row, 5, wrapper)
            self._checkboxes.append(cb)
