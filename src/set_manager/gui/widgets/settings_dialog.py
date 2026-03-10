"""Service settings dialog — Spotify Client ID and Tidal session management."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from set_manager.utils import config


class SettingsDialog(QDialog):
    """Service settings: Spotify Developer App credentials and Tidal session management."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Service Settings")
        self.setMinimumWidth(480)

        root = QVBoxLayout(self)
        root.addWidget(self._build_spotify_group())
        root.addWidget(self._build_tidal_group())

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

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

        info = QLabel(
            "No credentials needed — Tidal uses a device-code login flow. "
            "Click <b>Connect to Tidal</b> in the Import dialog to authenticate; "
            "a browser link will appear in the console."
        )
        info.setWordWrap(True)

        self._clear_tidal_btn = QPushButton("Clear Cached Tidal Session")
        self._clear_tidal_btn.clicked.connect(self._on_clear_tidal_session)

        layout.addWidget(info)
        layout.addWidget(self._clear_tidal_btn)
        return box

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_accept(self) -> None:
        client_id = self._client_id.text().strip()
        config.set("spotify_client_id", client_id)
        self.accept()

    def _on_clear_tidal_session(self) -> None:
        from pathlib import Path

        import platformdirs

        session_file = Path(platformdirs.user_cache_dir("set_manager")) / "tidal_session.json"
        if session_file.exists():
            session_file.unlink()
            self._clear_tidal_btn.setText("Session cleared")
        else:
            self._clear_tidal_btn.setText("No session on disk")
