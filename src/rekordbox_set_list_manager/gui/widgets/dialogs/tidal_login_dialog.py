"""Modal embedded-browser login for Tidal's PKCE OAuth flow."""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

# Tidal's PKCE authorize endpoint always redirects to this registered URL,
# which a desktop app cannot claim — so the embedded page cancels the
# navigation and captures the auth code from the URL instead.
REDIRECT_PREFIX = "https://tidal.com/android/login/auth"

# Tidal's login sits behind bot protection that inspects the user agent;
# present a plain Chrome UA so the embedded Chromium is treated as a
# normal browser.
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


class _InterceptPage(QWebEnginePage):
    """Page that captures main-frame navigation to the OAuth redirect URL."""

    redirect_captured = Signal(str)

    def acceptNavigationRequest(
        self,
        url: QUrl | str,
        nav_type: QWebEnginePage.NavigationType,
        is_main_frame: bool,
    ) -> bool:
        url_str = url.toString() if isinstance(url, QUrl) else url
        if is_main_frame and url_str.startswith(REDIRECT_PREFIX):
            self.redirect_captured.emit(url_str)
            return False
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)


class TidalLoginDialog(QDialog):
    """Embedded-browser dialog that completes the Tidal PKCE login in-app.

    Loads the PKCE authorize URL in a Chromium view; when Tidal redirects
    to its fixed ``tidal.com/android/login/auth`` URL the navigation is
    intercepted, the auth code captured, and the dialog accepted — no
    manual URL pasting needed.  A paste field remains as a fallback for
    users whose login must happen in an external browser instead.

    Attributes
    ----------
    redirect_url : str | None
        The captured (or pasted) redirect URL once the dialog is accepted.

    """

    def __init__(self, login_url: str, parent: QWidget | None = None) -> None:
        """Open the embedded login at *login_url*."""
        super().__init__(parent)
        self.setWindowTitle("Log in to Tidal")
        self.resize(520, 720)
        self.redirect_url: str | None = None

        page = _InterceptPage(self)
        page.redirect_captured.connect(self._on_captured)
        QWebEngineProfile.defaultProfile().setHttpUserAgent(_USER_AGENT)
        view = QWebEngineView(self)
        view.setPage(page)
        view.load(QUrl(login_url))

        fallback_hint = QLabel(
            "Login not working here? "
            f'<a href="{login_url}">Open it in your browser</a> instead, '
            "then paste the address of the page you land on:"
        )
        fallback_hint.setTextFormat(Qt.TextFormat.RichText)
        fallback_hint.setOpenExternalLinks(True)
        fallback_hint.setWordWrap(True)

        self._paste_input = QLineEdit()
        self._paste_input.setPlaceholderText(f"{REDIRECT_PREFIX}?code=…")
        self._paste_input.setClearButtonEnabled(True)
        self._paste_input.setMinimumHeight(30)
        self._paste_input.returnPressed.connect(self._on_paste_submit)
        paste_btn = QPushButton("Use This URL")
        paste_btn.clicked.connect(self._on_paste_submit)

        paste_row = QHBoxLayout()
        paste_row.addWidget(self._paste_input, 1)
        paste_row.addWidget(paste_btn)

        self._paste_error = QLabel("")
        self._paste_error.setStyleSheet("color: #f38ba8;")
        self._paste_error.setVisible(False)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_row = QHBoxLayout()
        button_row.addStretch()
        button_row.addWidget(cancel_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(view, 1)
        layout.addWidget(fallback_hint)
        layout.addLayout(paste_row)
        layout.addWidget(self._paste_error)
        layout.addLayout(button_row)

    def _on_captured(self, url: str) -> None:
        self.redirect_url = url
        self.accept()

    def _on_paste_submit(self) -> None:
        url = self._paste_input.text().strip()
        if url.startswith(REDIRECT_PREFIX):
            self.redirect_url = url
            self.accept()
            return
        self._paste_error.setText(
            "That doesn't look like the Tidal login result — "
            "copy the full address of the page you land on after logging in."
        )
        self._paste_error.setVisible(True)


def run_tidal_login(login_url: str, parent: QWidget | None = None) -> str | None:
    """Open the embedded Tidal login dialog and return the redirect URL.

    Returns
    -------
    str | None
        The captured redirect URL on success, or ``None`` if the user
        cancelled the login.
    """
    dialog = TidalLoginDialog(login_url, parent)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        return dialog.redirect_url
    return None
