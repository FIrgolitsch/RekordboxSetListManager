"""Generic streaming service tab: connect, browse playlists, select."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from rekordbox_set_list_manager.gui.widgets.common.busy_dialog import BusyDialog, show_error_dialog
from rekordbox_set_list_manager.gui.widgets.streaming.auth_worker import StreamingAuthWorker

if TYPE_CHECKING:
    from rekordbox_set_list_manager.services.streaming_base import (
        PlaylistResult,
        StreamingService,
    )


class StreamingServiceTab(QWidget):
    """Reusable tab for a single streaming service (Spotify or Tidal).

    Emits :attr:`tracks_loaded` when the user selects a playlist, passing
    ``(tracks, skipped_items, playlist_id)``.
    """

    tracks_loaded = Signal(list, list, str)  # tracks, skipped items, playlist_id

    def __init__(self, service: StreamingService, parent: QWidget | None = None) -> None:
        """Initialise the streaming service tab for *service*."""
        super().__init__(parent)
        self._service = service
        self._playlist_ids: list[str] = []
        self._auto_select_id: str | None = None
        self._thread: QThread | None = None
        self._worker: StreamingAuthWorker | None = None

        # ── Connect row ──────────────────────────────────────────────────
        self._connect_btn = QPushButton("Connect")
        self._auth_label = QLabel("Not connected")
        auth_row = QHBoxLayout()
        auth_row.addWidget(self._connect_btn)
        auth_row.addWidget(self._auth_label)
        auth_row.addStretch()

        # ── Login link (Tidal only; hidden until needed) ─────────────────
        self._link_label = QLabel("")
        self._link_label.setTextFormat(Qt.TextFormat.RichText)
        self._link_label.setOpenExternalLinks(True)
        self._link_label.setWordWrap(True)
        self._link_label.setVisible(False)

        # ── PKCE redirect paste-back (Tidal only; hidden until needed) ───
        self._redirect_hint = QLabel(
            "After logging in, the browser lands on an 'Oops' page — "
            "copy its full address here to finish connecting."
        )
        self._redirect_hint.setWordWrap(True)
        self._redirect_hint.setVisible(False)

        self._redirect_input = QLineEdit()
        self._redirect_input.setPlaceholderText("https://tidal.com/android/login/auth?code=…")
        self._redirect_btn = QPushButton("Complete Login")
        self._redirect_btn.clicked.connect(self._on_redirect_submit)
        self._redirect_input.returnPressed.connect(self._on_redirect_submit)

        redirect_row = QHBoxLayout()
        redirect_row.addWidget(self._redirect_input, 1)
        redirect_row.addWidget(self._redirect_btn)
        self._redirect_container = QWidget()
        self._redirect_container.setLayout(redirect_row)
        self._redirect_container.setVisible(False)

        # ── Playlist list ────────────────────────────────────────────────
        self._playlist_widget = QListWidget()
        self._playlist_widget.itemClicked.connect(self._on_playlist_clicked)

        layout = QVBoxLayout(self)
        layout.addLayout(auth_row)
        layout.addWidget(self._link_label)
        layout.addWidget(self._redirect_hint)
        layout.addWidget(self._redirect_container)
        layout.addWidget(self._playlist_widget)

        self._connect_btn.clicked.connect(self._on_connect)

    def try_auto_connect(self, auto_select_id: str | None = None) -> None:
        """Silently reconnect using cached credentials on a background thread.

        The silent path performs real network I/O (token validation), so it
        runs on the same worker thread as interactive auth — a slow or
        absent network must not freeze dialog construction.
        """
        self._auto_select_id = auto_select_id
        self._connect_btn.setEnabled(False)
        self._auth_label.setText("Connecting…")
        self._start_auth(silent=True)

    def stop_auth(self) -> None:
        """Cancel and wait briefly for any running auth thread (safe on dialog close)."""
        if self._worker is not None:
            self._worker.cancel()
        if self._thread is not None and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(3000)

    # ------------------------------------------------------------------
    # Private slots
    # ------------------------------------------------------------------

    def _on_connect(self) -> None:
        self._connect_btn.setEnabled(False)
        self._auth_label.setText("Connecting…")
        self._start_auth(silent=False)

    def _start_auth(self, silent: bool) -> None:
        # No parent: the thread may still be running (blocking auth) when the
        # widget tree is torn down; stop_auth cancels and joins it first.
        self._thread = QThread()
        self._worker = StreamingAuthWorker(self._service, silent=silent)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.link_ready.connect(self._on_link_ready)
        self._worker.redirect_requested.connect(self._on_redirect_requested)
        self._worker.finished.connect(self._on_auth_done)
        self._worker.error.connect(self._on_auth_error)
        self._worker.silent_failed.connect(self._on_silent_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._worker.silent_failed.connect(self._thread.quit)
        self._thread.finished.connect(self._thread.deleteLater)
        # Deleting the worker on thread.finished (not on finished/error)
        # also covers the cancelled path, where the worker emits nothing.
        self._thread.finished.connect(self._worker.deleteLater)
        # Clear the Python-side refs once the thread finishes — deleteLater
        # destroys the C++ objects, so any later isRunning()/cancel() call on
        # a stale ref crashes with a deleted-object RuntimeError.
        self._thread.finished.connect(self._clear_auth_refs)
        self._thread.start()

    def _clear_auth_refs(self) -> None:
        self._thread = None
        self._worker = None

    def _on_link_ready(self, msg: str) -> None:
        match = re.search(r"https?://\S+", msg)
        if match:
            url = match.group(0)
            self._link_label.setText(f'Open in browser to log in: <a href="{url}">{url}</a>')
        else:
            self._link_label.setText(msg)
        self._link_label.setVisible(True)
        self._auth_label.setText("Waiting for approval…")

    def _on_redirect_requested(self) -> None:
        """Show the paste-back field when the PKCE flow needs the redirect URL."""
        self._redirect_hint.setVisible(True)
        self._redirect_container.setVisible(True)
        self._redirect_input.setFocus()
        self._auth_label.setText("Paste the 'Oops' page URL to finish…")

    def _on_redirect_submit(self) -> None:
        url = self._redirect_input.text().strip()
        if not url or self._worker is None:
            return
        self._redirect_input.setEnabled(False)
        self._redirect_btn.setEnabled(False)
        self._worker.provide_redirect(url)

    def _hide_redirect_row(self) -> None:
        self._redirect_hint.setVisible(False)
        self._redirect_container.setVisible(False)
        self._redirect_input.clear()
        self._redirect_input.setEnabled(True)
        self._redirect_btn.setEnabled(True)

    def _on_auth_done(self, username: str) -> None:
        self._auth_label.setText(f"Connected as {username}")
        self._link_label.setVisible(False)
        self._link_label.setText("")
        self._hide_redirect_row()
        self._load_playlists()

    def _on_auth_error(self, msg: str) -> None:
        self._auth_label.setText("Connection failed")
        self._link_label.setVisible(False)
        self._hide_redirect_row()
        self._connect_btn.setEnabled(True)
        show_error_dialog(self, "Connection Error", msg)

    def _on_silent_failed(self) -> None:
        """No cached session to restore — stay disconnected without a dialog."""
        self._auth_label.setText("Not connected")
        self._hide_redirect_row()
        self._connect_btn.setEnabled(True)

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

        # Auto-select the playlist used in the previous import, if still present.
        if self._auto_select_id is not None:
            try:
                idx = self._playlist_ids.index(self._auto_select_id)
                item = self._playlist_widget.item(idx)
                if item is not None:
                    self._playlist_widget.setCurrentItem(item)
                    self._on_playlist_clicked(item)
            except ValueError:
                pass

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
        playlist_result: PlaylistResult = result  # type: ignore[assignment]
        self.tracks_loaded.emit(playlist_result.tracks, playlist_result.skipped, pl_id)
