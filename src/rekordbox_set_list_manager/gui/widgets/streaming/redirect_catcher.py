"""Auto-capture the Tidal PKCE redirect URL from browser tabs or clipboard."""

from __future__ import annotations

import subprocess
import sys
import threading

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QGuiApplication

# Tidal's PKCE authorize endpoint redirects to this registered URL, which a
# desktop app cannot claim — the browser lands on it and the address must
# come back to the app.
REDIRECT_PREFIX = "https://tidal.com/android/login/auth"

_CLIPBOARD_POLL_MS = 500
_BROWSER_POLL_SECONDS = 1.0
_SCAN_TIMEOUT_SECONDS = 4

# On macOS, JXA can enumerate the tabs of every running browser without
# launching ones that are absent.  Tidal's redirect lands in a tab, so
# polling picks it up the moment it appears — the user never copies or
# pastes anything.  Browsers without a tab model (Firefox) and non-macOS
# platforms are covered by the clipboard watcher instead.
_BROWSER_SCAN_JS = (
    'const target = "' + REDIRECT_PREFIX + '";\n'
    "const hits = [];\n"
    'const browsers = ["com.apple.Safari", "com.google.Chrome", "com.brave.Browser",'
    ' "com.microsoft.edgemac", "company.thebrowser.Browser", "org.chromium.Chromium",'
    ' "com.vivaldi.Vivaldi", "com.operasoftware.Opera"];\n'
    "for (const bid of browsers) {\n"
    "    try {\n"
    "        const app = Application(bid);\n"
    "        if (!app.running()) continue;\n"
    "        for (const win of app.windows()) {\n"
    "            try {\n"
    "                for (const tab of win.tabs()) {\n"
    "                    const u = tab.url();\n"
    "                    if (u && u.startsWith(target)) hits.push(u);\n"
    "                }\n"
    "            } catch (e) {}\n"
    "        }\n"
    "    } catch (e) {}\n"
    "}\n"
    'hits.join("\\n");\n'
)


def scan_browser_tabs() -> set[str]:
    """Return the redirect URLs currently open in any running browser tab.

    macOS only; returns an empty set elsewhere, on error, or when no
    browser exposes a matching tab.  Automation consent prompts may appear
    once per browser — a denial just means that browser is skipped.
    """
    if sys.platform != "darwin":
        return set()
    try:
        proc = subprocess.run(  # noqa: S603 — fixed args, no shell
            ["/usr/bin/osascript", "-l", "JavaScript", "-e", _BROWSER_SCAN_JS],
            capture_output=True,
            text=True,
            timeout=_SCAN_TIMEOUT_SECONDS,
            check=False,
        )
    except OSError, subprocess.TimeoutExpired:
        return set()
    return {
        line.strip()
        for line in proc.stdout.splitlines()
        if line.strip().startswith(REDIRECT_PREFIX)
    }


class RedirectCatcher(QObject):
    """Emits ``found`` when the Tidal redirect URL is captured.

    Two capture channels run while active: a clipboard watcher (all
    platforms) and, on macOS, a background poll of running browsers' tabs.
    Anything already present when :meth:`start` runs is treated as a
    baseline and ignored — a redirect URL that predates the login belongs
    to a dead code verifier and would fail the token exchange.
    """

    found = Signal(str)
    _found_internal = Signal(str)  # cross-thread handoff into the GUI thread

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setInterval(_CLIPBOARD_POLL_MS)
        self._timer.timeout.connect(self._poll_clipboard)
        self._found_internal.connect(self._finish)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._baseline_clipboard = ""

    def start(self) -> None:
        """Begin watching for the redirect URL."""
        clipboard = QGuiApplication.clipboard()
        self._baseline_clipboard = clipboard.text().strip() if clipboard else ""
        self._stop_event.clear()
        self._timer.start()
        if sys.platform == "darwin" and (self._thread is None or not self._thread.is_alive()):
            self._thread = threading.Thread(target=self._scan_loop, daemon=True)
            self._thread.start()

    def stop(self) -> None:
        """Stop watching; the catcher can be started again later."""
        self._timer.stop()
        self._stop_event.set()

    def _finish(self, url: str) -> None:
        """Handle a captured URL on the GUI thread (first match wins)."""
        if self._stop_event.is_set():
            return
        self.stop()
        self.found.emit(url)

    def _poll_clipboard(self) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is None or self._stop_event.is_set():
            return
        text = clipboard.text().strip()
        if text.startswith(REDIRECT_PREFIX) and text != self._baseline_clipboard:
            self._found_internal.emit(text)

    def _scan_loop(self) -> None:
        # Baseline: redirect tabs already open when the login started hold
        # codes minted for a previous (dead) verifier — ignore them.
        baseline = scan_browser_tabs()
        while not self._stop_event.wait(_BROWSER_POLL_SECONDS):
            new = scan_browser_tabs() - baseline
            if new:
                self._found_internal.emit(new.pop())
                return
