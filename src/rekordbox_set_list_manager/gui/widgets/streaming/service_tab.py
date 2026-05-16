"""Generic streaming service tab: connect, browse playlists, select."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from rekordbox_set_list_manager.gui.widgets.common.busy_dialog import BusyDialog, show_error_dialog
from rekordbox_set_list_manager.gui.widgets.streaming.auth_worker import StreamingAuthWorker

if TYPE_CHECKING:
    from rekordbox_set_list_manager.models.track import Track
    from rekordbox_set_list_manager.services.streaming_base import StreamingService


class StreamingServiceTab(QWidget):
    """Reusable tab for a single streaming service (Spotify or Tidal).

    Emits :attr:`tracks_loaded` when the user selects a playlist, passing
    ``(tracks, skipped_count, playlist_id)``.
    """

    tracks_loaded = Signal(list, int, str)  # tracks, skipped, playlist_id

    def __init__(self, service: StreamingService, parent: QWidget | None = None) -> None:
        """Initialise the streaming service tab for *service*."""
        super().__init__(parent)
        self._service = service
        self._playlist_ids: list[str] = []

        # ── Connect row ──────────────────────────────────────────────────
        self._connect_btn = QPushButton("Connect")
        self._auth_label = QLabel("Not connected")
        auth_row = QHBoxLayout()
        auth_row.addWidget(self._connect_btn)
        auth_row.addWidget(self._auth_label)
        auth_row.addStretch()

        # ── Device-code link (Tidal only; hidden until needed) ───────────
        self._link_label = QLabel("")
        self._link_label.setTextFormat(Qt.TextFormat.RichText)
        self._link_label.setOpenExternalLinks(True)
        self._link_label.setWordWrap(True)
        self._link_label.setVisible(False)

        # ── Playlist list ────────────────────────────────────────────────
        self._playlist_widget = QListWidget()
        self._playlist_widget.itemClicked.connect(self._on_playlist_clicked)

        layout = QVBoxLayout(self)
        layout.addLayout(auth_row)
        layout.addWidget(self._link_label)
        layout.addWidget(self._playlist_widget)

        self._connect_btn.clicked.connect(self._on_connect)

    def try_auto_connect(self) -> None:
        """Silently reconnect using cached credentials if available."""
        name = self._service.try_silent_authenticate()
        if name:
            self._auth_label.setText(f"Connected as {name}")
            self._connect_btn.setEnabled(False)
            self._load_playlists()

    # ------------------------------------------------------------------
    # Private slots
    # ------------------------------------------------------------------

    def _on_connect(self) -> None:
        self._connect_btn.setEnabled(False)
        self._auth_label.setText("Connecting…")

        self._thread = QThread(self)
        self._worker = StreamingAuthWorker(self._service)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.link_ready.connect(self._on_link_ready)
        self._worker.finished.connect(self._on_auth_done)
        self._worker.error.connect(self._on_auth_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._thread.start()

    def _on_link_ready(self, msg: str) -> None:
        match = re.search(r"https?://\S+", msg)
        if match:
            url = match.group(0)
            self._link_label.setText(f'Open in browser to log in: <a href="{url}">{url}</a>')
        else:
            self._link_label.setText(msg)
        self._link_label.setVisible(True)
        self._auth_label.setText("Waiting for approval…")

    def _on_auth_done(self, username: str) -> None:
        self._auth_label.setText(f"Connected as {username}")
        self._link_label.setVisible(False)
        self._link_label.setText("")
        self._load_playlists()

    def _on_auth_error(self, msg: str) -> None:
        self._auth_label.setText("Connection failed")
        self._link_label.setVisible(False)
        self._connect_btn.setEnabled(True)
        show_error_dialog(self, "Connection Error", msg)

    def _load_playlists(self) -> None:
        dlg = BusyDialog("Loading playlists\u2026", self, cancellable=False)
        ok, result, error = dlg.run(self._service.get_playlists)
        if not ok:
            if error:
                show_error_dialog(self, "Error loading playlists", error)
            return
        playlists: list[dict] = result  # type: ignore[assignment]
        self._playlist_ids = [pl["id"] for pl in playlists]
        self._playlist_widget.blockSignals(True)
        self._playlist_widget.clear()
        for pl in playlists:
            label = pl["name"]
            if "track_count" in pl:
                label += f"  ({pl['track_count']})"
            self._playlist_widget.addItem(label)
        self._playlist_widget.blockSignals(False)

    def _on_playlist_clicked(self, item: QListWidgetItem) -> None:
        row = self._playlist_widget.row(item)
        if row < 0 or row >= len(self._playlist_ids):
            return
        pl_id = self._playlist_ids[row]
        dlg = BusyDialog("Loading tracks\u2026", self, cancellable=True)
        ok, result, error = dlg.run(lambda: self._service.get_playlist_tracks(pl_id))
        if not ok:
            if error:
                show_error_dialog(self, "Error loading tracks", error)
            return
        tracks: list[Track]
        skipped: int
        tracks, skipped = result  # type: ignore[assignment]
        self.tracks_loaded.emit(tracks, skipped, pl_id)
