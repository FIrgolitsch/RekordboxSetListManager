"""Dialog for manually reviewing and fixing track matches against Rekordbox."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from rekordbox_set_list_manager.gui.widgets.streaming.collection_browser import (
    CollectionBrowserWidget,
)
from rekordbox_set_list_manager.models.enums import MatchStatus
from rekordbox_set_list_manager.models.project import Project
from rekordbox_set_list_manager.models.track import Track
from rekordbox_set_list_manager.services.track_matcher import MatchStrategy, TrackMatcher

_STRATEGY_LABELS = {
    MatchStrategy.ISRC: "ISRC",
    MatchStrategy.EXACT: "Exact",
    MatchStrategy.FUZZY: "Fuzzy",
    MatchStrategy.FILENAME: "Filename",
    MatchStrategy.NONE: "—",
}

_STATUS_LABELS = {
    "unmatched": "Unmatched",
    "matched": "Matched",
    "manually_matched": "Manually matched",
    "conflicted": "Conflicted",
}

_STATUS_COLORS = {
    "unmatched": QColor(255, 180, 180, 80),  # red tint
    "matched": QColor(180, 230, 180, 80),  # green tint
    "manually_matched": QColor(180, 210, 255, 80),  # blue tint
    "conflicted": QColor(255, 200, 100, 80),  # orange tint
}


class RematchDialog(QDialog):
    """Show all project tracks and let the user manually match them against
    a loaded Rekordbox collection.

    Supports auto-matching (TrackMatcher) as a starting point, then manual
    overrides via browse-and-select.
    """

    def __init__(self, project: Project, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Manual Re-match with Rekordbox")
        self.setMinimumSize(1000, 650)
        self._project = project
        self._pending: dict[UUID, Track | None] = {}  # track_id → chosen local track

        # ── Load status ────────────────────────────────────────────────────
        self._load_status = QLabel("No collection loaded.")

        # ── Project tracks table ───────────────────────────────────────────
        self._proj_box = QGroupBox("Project Tracks")
        proj_layout = QVBoxLayout(self._proj_box)

        self._unmatched_only_cb = QCheckBox("Show only unmatched")
        self._unmatched_only_cb.toggled.connect(self._populate_proj_table)
        proj_layout.addWidget(self._unmatched_only_cb)

        from PySide6.QtWidgets import QHeaderView  # noqa: PLC0415

        self._proj_table = QTableWidget()
        self._proj_table.setColumnCount(6)
        self._proj_table.setHorizontalHeaderLabels(
            ["Title", "Artist", "Status", "Strategy", "Matched File", "New Match"]
        )
        hdr = self._proj_table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        hdr.resizeSection(0, 240)
        hdr.resizeSection(1, 200)
        hdr.resizeSection(3, 80)
        self._proj_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._proj_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self._proj_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._proj_table.verticalHeader().setVisible(False)
        self._proj_table.itemSelectionChanged.connect(self._on_proj_selection_changed)
        proj_layout.addWidget(self._proj_table)

        # ── Auto-match button ──────────────────────────────────────────────
        auto_row = QHBoxLayout()
        self._btn_auto_match = QPushButton("Auto-Match All")
        self._btn_auto_match.setToolTip(
            "Run TrackMatcher against the loaded collection and pre-fill results"
        )
        self._btn_auto_match.clicked.connect(self._auto_match)
        self._btn_auto_match.setEnabled(False)
        self._btn_clear_all = QPushButton("Clear All Matches")
        self._btn_clear_all.setToolTip("Remove all pending manual match assignments")
        self._btn_clear_all.clicked.connect(self._clear_all_matches)
        auto_row.addWidget(self._btn_auto_match)
        auto_row.addWidget(self._btn_clear_all)
        auto_row.addStretch()

        # ── Collection browser ─────────────────────────────────────────────
        self._browser = CollectionBrowserWidget()
        self._browser.collection_loaded.connect(self._on_collection_loaded)
        self._browser.track_selected.connect(lambda _: self._update_match_button())

        # ── Match action ───────────────────────────────────────────────────
        match_row = QHBoxLayout()
        self._match_info = QLabel(
            "Select a project track and a collection track, then click Match."
        )
        match_row.addWidget(self._match_info)
        match_row.addStretch()
        self._btn_match = QPushButton("→ Match Selected")
        self._btn_match.setEnabled(False)
        self._btn_match.clicked.connect(self._apply_manual_match)
        match_row.addWidget(self._btn_match)

        # ── Splitter ───────────────────────────────────────────────────────
        top = QWidget()
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.addWidget(self._proj_box)
        top_layout.addLayout(auto_row)

        bottom = QWidget()
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.addWidget(self._browser, 1)
        bottom_layout.addWidget(self._load_status)
        bottom_layout.addLayout(match_row)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(top)
        splitter.addWidget(bottom)
        splitter.setSizes([350, 250])

        # ── Dialog buttons ─────────────────────────────────────────────────
        self._btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._btn_box.accepted.connect(self.accept)
        self._btn_box.rejected.connect(self.reject)

        # ── Root layout ────────────────────────────────────────────────────
        root = QVBoxLayout(self)
        root.addWidget(splitter, 1)
        root.addWidget(self._btn_box)

        # Initial population
        self._populate_proj_table()

    # ── public ─────────────────────────────────────────────────────────────

    def pending_matches(self) -> dict[UUID, Track | None]:
        """Return track_id → matched local Track (or None to clear match)."""
        return dict(self._pending)

    # ── collection loading ─────────────────────────────────────────────────

    def _on_collection_loaded(self, count: int) -> None:
        self._load_status.setText(f"{count} track(s) loaded.")
        self._btn_auto_match.setEnabled(True)

    # ── project table ──────────────────────────────────────────────────────

    def _populate_proj_table(self) -> None:
        all_tracks = list(self._project.tracks.values())
        unmatched_only = self._unmatched_only_cb.isChecked()
        if unmatched_only:
            tracks = [t for t in all_tracks if t.match_status == MatchStatus.UNMATCHED]
        else:
            tracks = all_tracks

        matched_statuses = (MatchStatus.MATCHED, MatchStatus.MANUALLY_MATCHED)
        matched_count = sum(
            1 for t in all_tracks if t.match_status in matched_statuses
        )
        unmatched_count = len(all_tracks) - matched_count
        self._proj_table.setRowCount(len(tracks))
        for row, track in enumerate(tracks):
            status = track.match_status
            status_label = _STATUS_LABELS.get(str(status), str(status))
            status_color = _STATUS_COLORS.get(str(status))

            if status == MatchStatus.MATCHED:
                strategy = "auto"
            elif status == MatchStatus.MANUALLY_MATCHED:
                strategy = "manual"
            elif status == MatchStatus.UNMATCHED:
                strategy = "—"
            else:
                strategy = str(status)

            matched_file = (
                Path(track.filepath).name
                if track.filepath and track.match_status != MatchStatus.UNMATCHED
                else "—"
            )

            row_data = [
                track.title,
                track.artist,
                status_label,
                strategy,
                matched_file,
            ]
            for col, text in enumerate(row_data):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, track.id)
                if status_color is not None:
                    item.setBackground(status_color)
                self._proj_table.setItem(row, col, item)

            new_match_text = ""
            if track.id in self._pending:
                pending = self._pending[track.id]
                new_match_text = (
                    f"{pending.artist} - {pending.title}" if pending else "✗ Cleared"
                )
            item = QTableWidgetItem(new_match_text)
            item.setData(Qt.ItemDataRole.UserRole, track.id)
            if status_color is not None:
                item.setBackground(status_color)
            self._proj_table.setItem(row, 5, item)

        self._proj_box.setTitle(
            f"Project Tracks — {matched_count} matched, {unmatched_count} unmatched"
        )

    def _on_proj_selection_changed(self) -> None:
        self._update_match_button()

    # ── match logic ────────────────────────────────────────────────────────

    def _update_match_button(self) -> None:
        proj_sel = self._proj_table.selectedItems()
        self._btn_match.setEnabled(
            bool(proj_sel)
            and self._browser.selected_track is not None
            and len(self._browser.collection) > 0
        )

    def _apply_manual_match(self) -> None:
        proj_items = self._proj_table.selectedItems()
        local_track = self._browser.selected_track
        if not proj_items or local_track is None:
            return
        track_id: UUID = proj_items[0].data(Qt.ItemDataRole.UserRole)
        self._pending[track_id] = local_track
        self._populate_proj_table()

    def _auto_match(self) -> None:
        if not self._browser.collection:
            return
        reply = QMessageBox.question(
            self,
            "Auto-Match All",
            "This will overwrite all pending matches, including any you've set manually "
            "in this dialog. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        all_tracks = list(self._project.tracks.values())
        results = TrackMatcher().match(all_tracks, self._browser.collection)
        for result in results:
            if result.local_track is not None:
                self._pending[result.spotify_track.id] = result.local_track
        self._populate_proj_table()

    def _clear_all_matches(self) -> None:
        self._pending.clear()
        self._populate_proj_table()
