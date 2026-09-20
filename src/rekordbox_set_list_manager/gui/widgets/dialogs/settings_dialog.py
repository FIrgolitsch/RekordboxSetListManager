"""Service settings dialog — Spotify Client ID and Tidal session management."""

from __future__ import annotations

import re
from pathlib import Path

import platformdirs
from PySide6.QtCore import Qt, QThread, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from rekordbox_set_list_manager.gui.widgets.common.busy_dialog import show_error_dialog
from rekordbox_set_list_manager.gui.widgets.streaming.auth_worker import StreamingAuthWorker
from rekordbox_set_list_manager.gui.widgets.streaming.redirect_catcher import RedirectCatcher
from rekordbox_set_list_manager.services.tidal_service import TidalService
from rekordbox_set_list_manager.utils import config


class SettingsDialog(QDialog):
    """Service settings: Spotify Developer App credentials and Tidal session management."""

    def __init__(self, parent=None) -> None:
        """Initialise the service settings dialog."""
        super().__init__(parent)
        self.setWindowTitle("Service Settings")
        self.setMinimumWidth(480)

        self._tidal_service = TidalService()
        self._tidal_thread: QThread | None = None
        self._tidal_worker: StreamingAuthWorker | None = None
        self._redirect_catcher = RedirectCatcher(self)
        self._redirect_catcher.found.connect(self._on_tidal_redirect_captured)

        root = QVBoxLayout(self)
        root.addWidget(self._build_spotify_group())
        root.addWidget(self._build_tidal_group())
        root.addWidget(self._build_telemetry_group())

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        # Reflect any cached Tidal session without blocking dialog open.
        self._start_tidal_auth(silent=True)

    # ------------------------------------------------------------------
    # Group builders
    # ------------------------------------------------------------------

    def _build_spotify_group(self) -> QGroupBox:
        box = QGroupBox("Spotify")
        layout = QVBoxLayout(box)

        self._client_id = QLineEdit()
        self._client_id.setPlaceholderText("e.g. a1b2c3d4e5f6…")
        self._client_id.setText(str(config.get("spotify_client_id", "")))

        form = QFormLayout()
        form.addRow("Client ID:", self._client_id)

        redirect_note = QLabel(
            "Add <b>http://127.0.0.1:8888/callback</b> as a Redirect URI "
            "in your Spotify Developer Dashboard app settings."
        )
        redirect_note.setWordWrap(True)

        layout.addLayout(form)
        layout.addWidget(redirect_note)
        return box

    def _build_tidal_group(self) -> QGroupBox:
        box = QGroupBox("Tidal")
        layout = QVBoxLayout(box)

        self._tidal_status = QLabel("Not connected")
        self._tidal_connect_btn = QPushButton("Connect to Tidal…")
        self._tidal_connect_btn.clicked.connect(self._on_tidal_connect)

        connect_row = QHBoxLayout()
        connect_row.addWidget(self._tidal_connect_btn)
        connect_row.addWidget(self._tidal_status)
        connect_row.addStretch()

        self._tidal_link = QLabel("")
        self._tidal_link.setTextFormat(Qt.TextFormat.RichText)
        self._tidal_link.setOpenExternalLinks(True)
        self._tidal_link.setWordWrap(True)
        self._tidal_link.setVisible(False)

        self._tidal_redirect_hint = QLabel(
            "Log in in your browser — when it lands on an 'Oops' page, "
            "we detect it automatically. If nothing happens, copy the page "
            "address or paste it below:"
        )
        self._tidal_redirect_hint.setWordWrap(True)
        self._tidal_redirect_hint.setVisible(False)

        self._tidal_redirect_input = QLineEdit()
        self._tidal_redirect_input.setPlaceholderText("https://tidal.com/android/login/auth?code=…")
        self._tidal_redirect_btn = QPushButton("Complete Login")
        self._tidal_redirect_btn.clicked.connect(self._on_tidal_redirect_submit)
        self._tidal_redirect_input.returnPressed.connect(self._on_tidal_redirect_submit)

        redirect_row = QHBoxLayout()
        redirect_row.addWidget(self._tidal_redirect_input, 1)
        redirect_row.addWidget(self._tidal_redirect_btn)
        self._tidal_redirect_row = QWidget()
        self._tidal_redirect_row.setLayout(redirect_row)
        self._tidal_redirect_row.setVisible(False)

        note = QLabel(
            "Connecting opens the Tidal login page in your browser — log in "
            "once and the session stays cached on this machine."
        )
        note.setWordWrap(True)

        self._clear_tidal_btn = QPushButton("Clear Cached Tidal Session")
        self._clear_tidal_btn.clicked.connect(self._on_clear_tidal_session)

        layout.addLayout(connect_row)
        layout.addWidget(self._tidal_link)
        layout.addWidget(self._tidal_redirect_hint)
        layout.addWidget(self._tidal_redirect_row)
        layout.addWidget(note)
        layout.addWidget(self._clear_tidal_btn)
        return box

    def _build_telemetry_group(self) -> QGroupBox:
        box = QGroupBox("Usage Data")
        layout = QVBoxLayout(box)

        note = QLabel(
            "When enabled, anonymous usage events (app_start, project_save, import counts, "
            "match strategy totals, error types) are written to a local log file. "
            "No file paths, track names, or personal data are ever collected."
        )
        note.setWordWrap(True)

        self._telemetry_cb = QCheckBox("Send anonymous usage data (opt-in)")
        self._telemetry_cb.setChecked(bool(config.get("telemetry_enabled", False)))

        layout.addWidget(note)
        layout.addWidget(self._telemetry_cb)
        return box

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_accept(self) -> None:
        client_id = self._client_id.text().strip()
        config.set_value("spotify_client_id", client_id)
        config.set_value("telemetry_enabled", self._telemetry_cb.isChecked())
        self.accept()

    def _on_clear_tidal_session(self) -> None:
        cache_dir = Path(platformdirs.user_cache_dir("rekordbox_set_list_manager"))
        session_file = cache_dir / "tidal_session.json"
        if session_file.exists():
            session_file.unlink()
            self._clear_tidal_btn.setText("Session cleared")
            self._tidal_status.setText("Not connected")
        else:
            self._clear_tidal_btn.setText("No session on disk")

    # ------------------------------------------------------------------
    # Tidal auth
    # ------------------------------------------------------------------

    def _on_tidal_connect(self) -> None:
        self._tidal_connect_btn.setEnabled(False)
        self._tidal_status.setText("Connecting…")
        self._start_tidal_auth(silent=False)

    def _start_tidal_auth(self, silent: bool) -> None:
        self._stop_tidal_auth()
        self._tidal_thread = QThread()
        self._tidal_worker = StreamingAuthWorker(self._tidal_service, silent=silent)
        self._tidal_worker.moveToThread(self._tidal_thread)
        self._tidal_thread.started.connect(self._tidal_worker.run)
        self._tidal_worker.link_ready.connect(self._on_tidal_link_ready)
        self._tidal_worker.redirect_requested.connect(self._on_tidal_redirect_requested)
        self._tidal_worker.finished.connect(self._on_tidal_auth_done)
        self._tidal_worker.error.connect(self._on_tidal_auth_error)
        self._tidal_worker.silent_failed.connect(self._on_tidal_silent_failed)
        self._tidal_worker.finished.connect(self._tidal_thread.quit)
        self._tidal_worker.error.connect(self._tidal_thread.quit)
        self._tidal_worker.silent_failed.connect(self._tidal_thread.quit)
        self._tidal_thread.finished.connect(self._tidal_thread.deleteLater)
        self._tidal_thread.finished.connect(self._tidal_worker.deleteLater)
        # Clear the Python-side refs once the thread finishes — deleteLater
        # destroys the C++ objects, so any later isRunning()/cancel() call on
        # a stale ref crashes with a deleted-object RuntimeError.
        self._tidal_thread.finished.connect(self._clear_tidal_auth_refs)
        self._tidal_thread.start()

    def _clear_tidal_auth_refs(self) -> None:
        self._tidal_thread = None
        self._tidal_worker = None

    def _stop_tidal_auth(self) -> None:
        self._redirect_catcher.stop()
        if self._tidal_worker is not None:
            self._tidal_worker.cancel()
        if self._tidal_thread is not None and self._tidal_thread.isRunning():
            self._tidal_thread.quit()
            self._tidal_thread.wait(3000)
        self._tidal_worker = None
        self._tidal_thread = None

    def _on_tidal_link_ready(self, msg: str) -> None:
        match = re.search(r"https?://\S+", msg)
        if match:
            url = match.group(0)
            self._tidal_link.setText(f'Login page: <a href="{url}">{url}</a>')
            QDesktopServices.openUrl(QUrl(url))
        else:
            self._tidal_link.setText(msg)
        self._tidal_link.setVisible(True)
        self._tidal_status.setText("Waiting for login…")

    def _on_tidal_redirect_requested(self) -> None:
        """Watch for the redirect URL — clipboard auto-detect plus paste."""
        self._redirect_catcher.start()
        self._tidal_redirect_hint.setVisible(True)
        self._tidal_redirect_row.setVisible(True)
        self._tidal_redirect_input.setFocus()
        self._tidal_status.setText("Finish the login in your browser…")

    def _on_tidal_redirect_captured(self, url: str) -> None:
        """Clipboard detected the redirect URL — hand it to the worker."""
        if self._tidal_worker is not None:
            self._tidal_status.setText("Finishing login…")
            self._tidal_worker.provide_redirect(url)

    def _on_tidal_redirect_submit(self) -> None:
        url = self._tidal_redirect_input.text().strip()
        if not url or self._tidal_worker is None:
            return
        self._tidal_redirect_input.setEnabled(False)
        self._tidal_redirect_btn.setEnabled(False)
        self._tidal_worker.provide_redirect(url)

    def _hide_tidal_redirect_row(self) -> None:
        self._redirect_catcher.stop()
        self._tidal_redirect_hint.setVisible(False)
        self._tidal_redirect_row.setVisible(False)
        self._tidal_redirect_input.clear()
        self._tidal_redirect_input.setEnabled(True)
        self._tidal_redirect_btn.setEnabled(True)

    def _on_tidal_auth_done(self, username: str) -> None:
        self._tidal_status.setText(f"Connected as {username}")
        self._tidal_link.setVisible(False)
        self._tidal_link.setText("")
        self._hide_tidal_redirect_row()
        self._tidal_connect_btn.setEnabled(True)

    def _on_tidal_auth_error(self, msg: str) -> None:
        self._tidal_status.setText("Connection failed")
        self._tidal_link.setVisible(False)
        self._hide_tidal_redirect_row()
        self._tidal_connect_btn.setEnabled(True)
        show_error_dialog(self, "Tidal Connection Error", msg)

    def _on_tidal_silent_failed(self) -> None:
        self._tidal_status.setText("Not connected")
        self._hide_tidal_redirect_row()
        self._tidal_connect_btn.setEnabled(True)

    # ------------------------------------------------------------------
    # Dialog lifecycle
    # ------------------------------------------------------------------

    def done(self, result: int) -> None:  # type: ignore[override]
        """Cancel any in-flight Tidal auth before the dialog closes."""
        self._stop_tidal_auth()
        super().done(result)
