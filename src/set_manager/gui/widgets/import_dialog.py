"""Streaming playlist import dialog with optional Rekordbox collection matching."""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from set_manager.gui.widgets.match_dialog import MatchDialog
from set_manager.models.project import Project
from set_manager.models.track import Track
from set_manager.services.rekordbox_db import RekordboxDbError, RekordboxDbService
from set_manager.services.rekordbox_xml import RekordboxXmlError, RekordboxXmlService
from set_manager.services.spotify_service import SpotifyService, SpotifyServiceError
from set_manager.services.tidal_service import TidalService, TidalServiceError
from set_manager.services.track_matcher import MatchResult, MatchStrategy, TrackMatcher

_TRACK_COLS = ["#", "Title", "Artist", "Duration", "ISRC", "Status"]


class _TidalAuthWorker(QObject):
    """Runs TidalService.authenticate() in a background thread."""

    link_ready = Signal(str)  # emitted with the URL message for display
    finished = Signal(str)    # emitted with the username on success
    error = Signal(str)       # emitted with the error message on failure

    def __init__(self, service: TidalService) -> None:
        super().__init__()
        self._service = service

    def run(self) -> None:
        try:
            name = self._service.authenticate(link_callback=self.link_ready.emit)
            self.finished.emit(name)
        except TidalServiceError as exc:
            self.error.emit(str(exc))


class ImportDialog(QDialog):
    """Import a playlist from Spotify or Tidal into the project.

    Flow:
    1. Select a service tab (Spotify or Tidal)
    2. Connect to the service — opens browser auth
    3. Select a playlist — tracks appear in the shared track preview
    4. (Optional) Load a Rekordbox collection (XML or local DB), then run "Match"
    5. "Import" — opens MatchDialog for review, then adds accepted tracks to project pool
    """

    def __init__(self, project: Project, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import from Streaming Service")
        self.setMinimumSize(880, 640)
        self._project = project
        self._spotify = SpotifyService()
        self._tidal = TidalService()
        self._matcher = TrackMatcher()
        self._rekordbox_service = RekordboxXmlService()
        self._db_service = RekordboxDbService()

        # Shared state
        self._playlist_tracks: list[Track] = []
        self._match_results: list[MatchResult] = []
        self._collection: list[Track] = []
        self._accepted: list[Track] = []
        self._skipped_count: int = 0

        # Per-service playlist ID lists
        self._spotify_playlist_ids: list[str] = []
        self._tidal_playlist_ids: list[str] = []

        self._build_ui()
        self._try_auto_connect()

    def results(self) -> list[Track]:
        """Tracks accepted by the user; call after exec() == Accepted."""
        return self._accepted

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # --- Service tabs ---
        tabs = QTabWidget()
        tabs.addTab(self._build_spotify_tab(), "Spotify")
        tabs.addTab(self._build_tidal_tab(), "Tidal")

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

        # --- Splitter: playlists (top) / track preview (bottom) ---
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(tabs)
        splitter.addWidget(tracks_box)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

        # --- Rekordbox row ---
        self._load_rb_btn = QPushButton("Load Rekordbox XML…")
        self._load_rb_btn.clicked.connect(self._on_load_rekordbox)
        self._load_db_btn = QPushButton("Auto-detect Rekordbox DB")
        self._load_db_btn.clicked.connect(self._on_load_rekordbox_db)
        self._rb_label = QLabel("No collection loaded")

        rb_row = QHBoxLayout()
        rb_row.addWidget(self._load_rb_btn)
        rb_row.addWidget(self._load_db_btn)
        rb_row.addWidget(self._rb_label)
        rb_row.addStretch()

        # --- Match row ---
        self._match_btn = QPushButton("Match with Collection")
        self._match_btn.clicked.connect(self._on_match)
        self._match_btn.setEnabled(False)
        self._match_label = QLabel("")

        match_row = QHBoxLayout()
        match_row.addWidget(self._match_btn)
        match_row.addWidget(self._match_label)
        match_row.addStretch()

        # --- Buttons ---
        self._ok_btn = QPushButton("Import")
        self._ok_btn.setEnabled(False)
        self._ok_btn.clicked.connect(self._on_import)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(self._ok_btn)

        # --- Root layout ---
        root = QVBoxLayout(self)
        root.addWidget(splitter, 1)
        root.addLayout(rb_row)
        root.addLayout(match_row)
        root.addLayout(btn_row)

    def _build_spotify_tab(self) -> QWidget:
        widget = QWidget()

        self._sp_connect_btn = QPushButton("Connect to Spotify")
        self._sp_connect_btn.clicked.connect(self._on_spotify_connect)
        self._sp_auth_label = QLabel("Not connected")

        auth_row = QHBoxLayout()
        auth_row.addWidget(self._sp_connect_btn)
        auth_row.addWidget(self._sp_auth_label)
        auth_row.addStretch()

        self._sp_playlist_widget = QListWidget()
        self._sp_playlist_widget.itemClicked.connect(self._on_spotify_playlist_selected)

        layout = QVBoxLayout(widget)
        layout.addLayout(auth_row)
        layout.addWidget(self._sp_playlist_widget)
        return widget

    def _build_tidal_tab(self) -> QWidget:
        widget = QWidget()

        self._td_connect_btn = QPushButton("Connect to Tidal")
        self._td_connect_btn.clicked.connect(self._on_tidal_connect)
        self._td_auth_label = QLabel("Not connected")

        auth_row = QHBoxLayout()
        auth_row.addWidget(self._td_connect_btn)
        auth_row.addWidget(self._td_auth_label)
        auth_row.addStretch()

        self._td_link_label = QLabel("")
        self._td_link_label.setTextFormat(Qt.TextFormat.RichText)
        self._td_link_label.setOpenExternalLinks(True)
        self._td_link_label.setWordWrap(True)

        self._td_playlist_widget = QListWidget()
        self._td_playlist_widget.itemClicked.connect(self._on_tidal_playlist_selected)

        layout = QVBoxLayout(widget)
        layout.addLayout(auth_row)
        layout.addWidget(self._td_link_label)
        layout.addWidget(self._td_playlist_widget)
        return widget

    # ------------------------------------------------------------------
    # Auto-connect
    # ------------------------------------------------------------------

    def _try_auto_connect(self) -> None:
        """Silently connect to services with cached credentials on dialog open."""
        name = self._spotify.try_silent_authenticate()
        if name:
            self._sp_auth_label.setText(f"Connected as {name}")
            self._sp_connect_btn.setEnabled(False)
            self._load_spotify_playlists()

        name = self._tidal.try_silent_authenticate()
        if name:
            self._td_auth_label.setText(f"Connected as {name}")
            self._td_connect_btn.setEnabled(False)
            self._load_tidal_playlists()

    # ------------------------------------------------------------------
    # Spotify slots
    # ------------------------------------------------------------------

    def _on_spotify_connect(self) -> None:
        self._sp_connect_btn.setEnabled(False)
        self._sp_auth_label.setText("Connecting…")
        try:
            display_name = self._spotify.authenticate()
            self._sp_auth_label.setText(f"Connected as {display_name}")
            self._load_spotify_playlists()
        except SpotifyServiceError as exc:
            self._sp_auth_label.setText("Connection failed")
            self._sp_connect_btn.setEnabled(True)
            QMessageBox.critical(self, "Spotify Error", str(exc))

    def _load_spotify_playlists(self) -> None:
        try:
            playlists = self._spotify.get_playlists()
        except SpotifyServiceError as exc:
            QMessageBox.critical(self, "Spotify Error", str(exc))
            return
        self._spotify_playlist_ids = [pl["id"] for pl in playlists]
        self._sp_playlist_widget.blockSignals(True)
        self._sp_playlist_widget.clear()
        for pl in playlists:
            self._sp_playlist_widget.addItem(pl["name"])
        self._sp_playlist_widget.blockSignals(False)

    def _on_spotify_playlist_selected(self, item: QListWidgetItem) -> None:
        row = self._sp_playlist_widget.row(item)
        if row < 0 or row >= len(self._spotify_playlist_ids):
            return
        try:
            self._playlist_tracks, self._skipped_count = self._spotify.get_playlist_tracks(
                self._spotify_playlist_ids[row]
            )
        except SpotifyServiceError as exc:
            QMessageBox.critical(self, "Spotify Error", str(exc))
            return
        except Exception as exc:
            QMessageBox.critical(self, "Spotify Error", f"Unexpected error loading tracks: {exc}")
            return
        self._on_tracks_loaded()

    # ------------------------------------------------------------------
    # Tidal slots
    # ------------------------------------------------------------------

    def _on_tidal_connect(self) -> None:
        self._td_connect_btn.setEnabled(False)
        self._td_auth_label.setText("Connecting…")
        self._td_link_label.setText("")

        self._tidal_thread = QThread(self)
        self._tidal_worker = _TidalAuthWorker(self._tidal)
        self._tidal_worker.moveToThread(self._tidal_thread)

        self._tidal_thread.started.connect(self._tidal_worker.run)
        self._tidal_worker.link_ready.connect(self._on_tidal_link_ready)
        self._tidal_worker.finished.connect(self._on_tidal_auth_done)
        self._tidal_worker.error.connect(self._on_tidal_auth_error)
        self._tidal_worker.finished.connect(self._tidal_thread.quit)
        self._tidal_worker.error.connect(self._tidal_thread.quit)

        self._tidal_thread.start()

    def _on_tidal_link_ready(self, msg: str) -> None:
        """Display the Tidal device-code login link in the dialog."""
        match = re.search(r"https?://\S+", msg)
        if match:
            url = match.group(0)
            self._td_link_label.setText(
                f'Open in browser to log in: <a href="{url}">{url}</a>'
            )
        else:
            self._td_link_label.setText(msg)
        self._td_auth_label.setText("Waiting for approval…")

    def _on_tidal_auth_done(self, username: str) -> None:
        self._td_auth_label.setText(f"Connected as {username}")
        self._td_link_label.setText("")
        self._load_tidal_playlists()

    def _on_tidal_auth_error(self, msg: str) -> None:
        self._td_auth_label.setText("Connection failed")
        self._td_link_label.setText("")
        self._td_connect_btn.setEnabled(True)
        QMessageBox.critical(self, "Tidal Error", msg)

    def _load_tidal_playlists(self) -> None:
        try:
            playlists = self._tidal.get_playlists()
        except TidalServiceError as exc:
            QMessageBox.critical(self, "Tidal Error", str(exc))
            return
        self._tidal_playlist_ids = [pl["id"] for pl in playlists]
        self._td_playlist_widget.blockSignals(True)
        self._td_playlist_widget.clear()
        for pl in playlists:
            self._td_playlist_widget.addItem(f"{pl['name']}  ({pl['track_count']})")
        self._td_playlist_widget.blockSignals(False)

    def _on_tidal_playlist_selected(self, item: QListWidgetItem) -> None:
        row = self._td_playlist_widget.row(item)
        if row < 0 or row >= len(self._tidal_playlist_ids):
            return
        try:
            self._playlist_tracks = self._tidal.get_playlist_tracks(
                self._tidal_playlist_ids[row]
            )
        except TidalServiceError as exc:
            QMessageBox.critical(self, "Tidal Error", str(exc))
            return
        except Exception as exc:
            QMessageBox.critical(self, "Tidal Error", f"Unexpected error loading tracks: {exc}")
            return
        self._skipped_count = 0
        self._on_tracks_loaded()

    # ------------------------------------------------------------------
    # Shared slots
    # ------------------------------------------------------------------

    def _on_tracks_loaded(self) -> None:
        """Called after any service populates self._playlist_tracks."""
        self._match_results = []
        self._refresh_track_table(self._playlist_tracks)
        count = len(self._playlist_tracks)
        if self._skipped_count:
            self._match_label.setText(
                f"{count} track(s) loaded, {self._skipped_count} skipped (no title / null)."
            )
        else:
            self._match_label.setText(f"{count} track(s) loaded.")
        self._update_import_button()
        if self._collection:
            self._match_btn.setEnabled(True)

    def _on_load_rekordbox(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self, "Open Rekordbox XML", "", "XML files (*.xml)"
        )
        if not path_str:
            return
        try:
            self._collection = self._rekordbox_service.import_collection(Path(path_str))
        except RekordboxXmlError as exc:
            QMessageBox.critical(self, "Rekordbox Error", str(exc))
            return
        self._rb_label.setText(f"{len(self._collection)} tracks loaded")
        if self._playlist_tracks:
            self._match_btn.setEnabled(True)

    def _on_load_rekordbox_db(self) -> None:
        self._load_db_btn.setEnabled(False)
        self._rb_label.setText("Detecting Rekordbox database…")
        try:
            self._collection = self._db_service.get_collection()
        except RekordboxDbError as exc:
            self._load_db_btn.setEnabled(True)
            self._rb_label.setText("No collection loaded")
            QMessageBox.critical(self, "Rekordbox DB Error", str(exc))
            return
        self._load_db_btn.setEnabled(True)
        self._rb_label.setText(f"{len(self._collection)} tracks loaded from Rekordbox DB")
        if self._playlist_tracks:
            self._match_btn.setEnabled(True)

    def _on_match(self) -> None:
        if not self._playlist_tracks or not self._collection:
            return
        self._match_results = self._matcher.match(self._playlist_tracks, self._collection)
        matched = sum(1 for r in self._match_results if r.strategy != MatchStrategy.NONE)
        unmatched = len(self._match_results) - matched
        self._match_label.setText(f"{matched} matched, {unmatched} unmatched")

        merged = [
            r.local_track if r.local_track is not None else r.spotify_track
            for r in self._match_results
        ]
        self._refresh_track_table(merged)
        self._update_import_button()

    def _on_import(self) -> None:
        if not self._playlist_tracks:
            return

        if not self._match_results:
            # No collection loaded — import all fetched tracks as-is.
            self._accepted = list(self._playlist_tracks)
            self.accept()
            return

        has_unmatched = any(r.strategy == MatchStrategy.NONE for r in self._match_results)
        if has_unmatched:
            dlg = MatchDialog(self._match_results, self)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            self._accepted = dlg.accepted_tracks()
        else:
            self._accepted = [
                r.local_track for r in self._match_results if r.local_track is not None
            ]

        self.accept()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _refresh_track_table(self, tracks: list[Track]) -> None:
        self._track_table.setRowCount(len(tracks))
        for row, track in enumerate(tracks):
            status = str(track.match_status).replace("_", " ").title()
            duration = track.duration_formatted or "—"
            row_data = [
                str(row + 1),
                track.title,
                track.artist,
                duration,
                track.isrc or "—",
                status,
            ]
            for col, text in enumerate(row_data):
                item = QTableWidgetItem(text)
                if col == 0:
                    item.setTextAlignment(int(Qt.AlignmentFlag.AlignCenter))
                self._track_table.setItem(row, col, item)

    def _update_import_button(self) -> None:
        count = len(self._playlist_tracks)
        self._ok_btn.setEnabled(count > 0)
        self._ok_btn.setText(f"Import {count} track(s)" if count else "Import")
