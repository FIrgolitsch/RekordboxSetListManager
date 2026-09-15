"""Streaming playlist import dialog with optional Rekordbox collection matching."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
)

from rekordbox_set_list_manager.gui.widgets.streaming.collection_browser import (
    CollectionBrowserWidget,
)
from rekordbox_set_list_manager.gui.widgets.streaming.service_tab import StreamingServiceTab
from rekordbox_set_list_manager.models.project import Project
from rekordbox_set_list_manager.models.track import Track
from rekordbox_set_list_manager.services.skipped_item import SkippedItem
from rekordbox_set_list_manager.services.spotify_service import SpotifyService
from rekordbox_set_list_manager.services.tidal_service import TidalService
from rekordbox_set_list_manager.services.track_matcher import (
    MatchResult,
    MatchStrategy,
    TrackMatcher,
)

_TRACK_COLS = ["#", "Title", "Artist", "Duration", "ISRC", "Status", "Matched File"]


class ImportDialog(QDialog):
    """Import a playlist from Spotify or Tidal into the project.

    Flow:
    1. Select a service tab (Spotify or Tidal) and connect
    2. Select a playlist — tracks appear in the shared track preview
    3. (Optional) Load a Rekordbox collection and run "Match"
    4. "Import" — adds matched (and optionally unmatched) tracks to project pool
    """

    def __init__(self, project: Project, parent=None) -> None:
        """Initialise the import dialog for *project*."""
        super().__init__(parent)
        self.setWindowTitle("Import from Streaming Service")
        self.setMinimumSize(880, 640)
        self._project = project
        self._spotify = SpotifyService()
        self._tidal = TidalService()
        self._matcher = TrackMatcher()

        # Shared state
        self._playlist_tracks: list[Track] = []
        self._match_results: list[MatchResult] = []
        self._accepted: list[Track] = []
        self._skipped_items: list[SkippedItem] = []
        self._isrc_duplicates: list[SkippedItem] = []
        self._selected_spotify_playlist_id: str | None = None
        self._selected_tidal_playlist_id: str | None = None

        self._build_ui()
        self._try_auto_connect()

    def results(self) -> list[Track]:
        """Tracks accepted by the user; call after exec() == Accepted."""
        return self._accepted

    @property
    def selected_spotify_playlist_id(self) -> str | None:
        """The Spotify playlist ID the user selected, if any."""
        return self._selected_spotify_playlist_id

    @property
    def selected_tidal_playlist_id(self) -> str | None:
        """The Tidal playlist ID the user selected, if any."""
        return self._selected_tidal_playlist_id

    @property
    def sync_mode(self) -> bool:
        """True when the user chose to add new tracks only (skip already imported)."""
        return self._sync_cb.isChecked()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # --- Service tabs ---
        self._spotify_tab = StreamingServiceTab(self._spotify)
        self._tidal_tab = StreamingServiceTab(self._tidal)
        self._spotify_tab.tracks_loaded.connect(self._on_spotify_tracks)
        self._tidal_tab.tracks_loaded.connect(self._on_tidal_tracks)

        tabs = QTabWidget()
        tabs.addTab(self._spotify_tab, "Spotify")
        tabs.addTab(self._tidal_tab, "Tidal")

        # --- Shared track preview ---
        self._track_table = QTableWidget(0, len(_TRACK_COLS))
        self._track_table.setHorizontalHeaderLabels(_TRACK_COLS)
        self._track_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._track_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self._track_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._track_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._track_table.verticalHeader().setVisible(False)

        tracks_box = QGroupBox("Selected Tracks")
        tr_layout = QVBoxLayout(tracks_box)
        tr_layout.setContentsMargins(4, 4, 4, 4)
        tr_layout.addWidget(self._track_table)

        # --- Splitter: service tabs / track preview ---
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(tabs)
        splitter.addWidget(tracks_box)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

        # --- Rekordbox collection browser ---
        self._collection_browser = CollectionBrowserWidget()
        self._collection_browser.collection_loaded.connect(self._on_collection_loaded)

        rb_box = QGroupBox("Rekordbox Collection")
        rb_layout = QVBoxLayout(rb_box)
        rb_layout.setContentsMargins(4, 4, 4, 4)
        rb_layout.addWidget(self._collection_browser)

        # --- Match row ---
        self._match_btn = QPushButton("Match with Collection")
        self._match_btn.clicked.connect(self._on_match)
        self._match_btn.setEnabled(False)
        self._match_label = QLabel("")
        self._details_btn = QPushButton("Skipped details…")
        self._details_btn.setToolTip(
            "Show which playlist entries were skipped and which duplicate ISRCs were resolved."
        )
        self._details_btn.setVisible(False)
        self._details_btn.clicked.connect(self._show_skipped_details)

        match_row = QHBoxLayout()
        match_row.addWidget(self._match_btn)
        match_row.addWidget(self._match_label)
        match_row.addWidget(self._details_btn)
        match_row.addStretch()

        # --- Sync checkbox (only useful when re-importing a known playlist) ---
        self._sync_cb = QCheckBox("New tracks only (skip already imported)")
        self._sync_cb.setToolTip(
            "When checked, only tracks not already in this project will be added."
        )
        # Show the checkbox when the project has a stored playlist ID.
        has_stored = bool(self._project.spotify_playlist_id or self._project.tidal_playlist_id)
        self._sync_cb.setVisible(has_stored)
        self._sync_cb.setChecked(has_stored)  # default ON when re-importing
        self._sync_cb.stateChanged.connect(self._update_import_button)

        # --- Buttons ---
        self._ok_btn = QPushButton("Import")
        self._ok_btn.setEnabled(False)
        self._ok_btn.clicked.connect(self._on_import)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self._sync_cb)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(self._ok_btn)

        # --- Root layout ---
        root = QVBoxLayout(self)
        root.addWidget(splitter, 1)
        root.addWidget(rb_box)
        root.addLayout(match_row)
        root.addLayout(btn_row)

    # ------------------------------------------------------------------
    # Auto-connect
    # ------------------------------------------------------------------

    def _try_auto_connect(self) -> None:
        """Silently connect to services with cached credentials on dialog open."""
        self._spotify_tab.try_auto_connect(self._project.spotify_playlist_id)
        self._tidal_tab.try_auto_connect(self._project.tidal_playlist_id)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        """Stop any running auth threads before closing."""
        self._spotify_tab.stop_auth()
        self._tidal_tab.stop_auth()
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_spotify_tracks(
        self, tracks: list[Track], skipped: list[SkippedItem], playlist_id: str
    ) -> None:
        self._selected_spotify_playlist_id = playlist_id
        self._selected_tidal_playlist_id = None
        self._skipped_items = skipped
        self._playlist_tracks = tracks
        # Show the sync checkbox when this is the playlist the project was imported from.
        is_known = playlist_id == self._project.spotify_playlist_id
        self._sync_cb.setVisible(is_known)
        self._sync_cb.setChecked(is_known)
        self._on_tracks_loaded()

    def _on_tidal_tracks(
        self, tracks: list[Track], skipped: list[SkippedItem], playlist_id: str
    ) -> None:
        self._selected_tidal_playlist_id = playlist_id
        self._selected_spotify_playlist_id = None
        self._skipped_items = skipped
        self._playlist_tracks = tracks
        # Show the sync checkbox when this is the playlist the project was imported from.
        is_known = playlist_id == self._project.tidal_playlist_id
        self._sync_cb.setVisible(is_known)
        self._sync_cb.setChecked(is_known)
        self._on_tracks_loaded()

    def _on_tracks_loaded(self) -> None:
        """Refresh the track table after any service tab populates self._playlist_tracks."""
        self._match_results = []
        self._isrc_duplicates = []
        self._refresh_track_table(self._playlist_tracks)
        count = len(self._playlist_tracks)
        skipped_count = len(self._skipped_items)
        if skipped_count:
            self._match_label.setText(
                f"{count} track(s) loaded, {skipped_count} skipped — see Skipped details."
            )
        else:
            self._match_label.setText(f"{count} track(s) loaded.")
        self._update_details_button()
        self._update_import_button()
        if self._collection_browser.collection:
            self._match_btn.setEnabled(True)

    def _on_collection_loaded(self, _count: int) -> None:
        self._match_results = []
        if self._playlist_tracks:
            self._match_btn.setEnabled(True)

    def _on_match(self) -> None:
        if not self._playlist_tracks:
            return
        self._match_results = self._matcher.match(
            self._playlist_tracks, self._collection_browser.collection
        )
        # Ambiguous-but-resolved ISRC groups go on the same details surface as
        # fetch-time skips so the user sees exactly what the matcher decided.
        self._isrc_duplicates = list(self._matcher.isrc_duplicates)
        matched = sum(1 for r in self._match_results if r.strategy != MatchStrategy.NONE)
        unmatched = len(self._match_results) - matched
        label = f"{matched} matched, {unmatched} unmatched (imported without local file)"
        if self._isrc_duplicates:
            label += f" — {len(self._isrc_duplicates)} duplicate ISRC group(s), see Skipped details"
        self._match_label.setText(label)
        self._update_details_button()
        merged = [
            r.local_track if r.local_track is not None else r.spotify_track
            for r in self._match_results
        ]
        matched_files = [
            Path(r.local_track.filepath).stem if r.local_track and r.local_track.filepath else None
            for r in self._match_results
        ]
        self._refresh_track_table(merged, matched_files)
        self._update_import_button()

    def _on_import(self) -> None:
        if not self._playlist_tracks:
            return

        if not self._match_results and not self._collection_browser.collection:
            # No collection loaded — import all fetched tracks as-is.
            self._accepted = list(self._playlist_tracks)
            self.accept()
            return

        if not self._match_results:
            # Collection was loaded but Match wasn't clicked — run it now.
            self._on_match()

        # Import all tracks: matched ones carry local file data, unmatched are
        # imported as streaming-only so the user sees what still needs downloading.
        self._accepted = [
            r.local_track if r.local_track is not None else r.spotify_track
            for r in self._match_results
        ]
        self.accept()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _update_details_button(self) -> None:
        self._details_btn.setVisible(bool(self._skipped_items or self._isrc_duplicates))

    def _skipped_details_text(self) -> str:
        """Itemized label — reason lines for the skipped/resolved report."""
        lines: list[str] = []
        if self._skipped_items:
            lines.append("Skipped playlist entries:")
            lines.extend(f"  {item.label} — {item.reason}" for item in self._skipped_items)
        if self._isrc_duplicates:
            if lines:
                lines.append("")
            lines.append("Duplicate ISRCs in collection:")
            lines.extend(f"  {item.label} — {item.reason}" for item in self._isrc_duplicates)
        return "\n".join(lines)

    def _show_skipped_details(self) -> None:
        """Show the itemized skipped/resolved report in a detailed-text message box."""
        if not (self._skipped_items or self._isrc_duplicates):
            return
        parts = []
        if self._skipped_items:
            parts.append(f"{len(self._skipped_items)} playlist entries were skipped")
        if self._isrc_duplicates:
            parts.append(
                f"{len(self._isrc_duplicates)} duplicate ISRC group(s) were resolved "
                "to the lowest Rekordbox ID"
            )
        msg_box = QMessageBox(
            QMessageBox.Icon.Information,
            "Skipped and resolved items",
            " and ".join(parts) + ".\nExpand below for the itemized list.",
            QMessageBox.StandardButton.Ok,
            self,
        )
        msg_box.setDetailedText(self._skipped_details_text())
        msg_box.exec()

    def _refresh_track_table(
        self, tracks: list[Track], matched_files: list[str | None] | None = None
    ) -> None:
        self._track_table.setRowCount(len(tracks))
        for row, track in enumerate(tracks):
            status = str(track.match_status).replace("_", " ").title()
            duration = track.duration_formatted or "—"
            matched = matched_files[row] if matched_files and matched_files[row] else "—"
            row_data = [
                str(row + 1),
                track.title,
                track.artist,
                duration,
                track.isrc or "—",
                status,
                matched,
            ]
            for col, text in enumerate(row_data):
                item = QTableWidgetItem(str(text))
                if col == 0:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._track_table.setItem(row, col, item)

    def _update_import_button(self) -> None:
        total = len(self._playlist_tracks)
        if self._sync_cb.isVisible() and self._sync_cb.isChecked():
            new_count = self._count_new_tracks()
            self._ok_btn.setEnabled(new_count > 0)
            self._ok_btn.setText(f"Add {new_count} new track(s)" if new_count else "No new tracks")
        else:
            self._ok_btn.setEnabled(total > 0)
            self._ok_btn.setText(f"Import {total} track(s)" if total else "Import")

    def _count_new_tracks(self) -> int:
        """Return how many playlist tracks are not yet in the project."""
        known = _existing_streaming_ids(self._project)
        return sum(1 for t in self._playlist_tracks if not _is_known(t, known))


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _existing_streaming_ids(project: Project) -> tuple[set[str], set[str], set[str]]:
    """Return (spotify_ids, tidal_ids, isrcs) already present in *project*."""
    spotify_ids: set[str] = set()
    tidal_ids: set[str] = set()
    isrcs: set[str] = set()
    for track in project.tracks.values():
        if track.spotify_id:
            spotify_ids.add(track.spotify_id)
        if track.tidal_id:
            tidal_ids.add(track.tidal_id)
        if track.isrc:
            isrcs.add(track.isrc)
    return spotify_ids, tidal_ids, isrcs


def _is_known(track: Track, known: tuple[set[str], set[str], set[str]]) -> bool:
    """Return True if *track* is already represented in the project."""
    spotify_ids, tidal_ids, isrcs = known
    if track.spotify_id and track.spotify_id in spotify_ids:
        return True
    if track.tidal_id and track.tidal_id in tidal_ids:
        return True
    return bool(track.isrc and track.isrc in isrcs)
