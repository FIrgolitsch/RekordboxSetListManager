"""Modal progress dialog for background operations."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt, QThread
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from rekordbox_set_list_manager.gui.widgets.common.qthread_worker import QThreadWorker


class BusyDialog(QDialog):
    """Modal indeterminate-progress dialog that runs a callable off the UI thread.

    Usage::

        dlg = BusyDialog("Loading…", parent, cancellable=True)
        ok, result, error = dlg.run(loader.load)
        if ok:
            self._collection = result
        elif error:
            QMessageBox.warning(self, "Failed", error)
    """

    def __init__(
        self,
        message: str,
        parent: QWidget | None = None,
        *,
        cancellable: bool = True,
    ) -> None:
        """Show *message* in a modal progress dialog."""
        super().__init__(parent)
        self.setWindowTitle("Please wait…")
        self.setModal(True)
        self.setMinimumWidth(300)
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)

        self._cancelled = False
        self._result: object = None
        self._error: str | None = None
        self._worker: QThreadWorker | None = None
        self._thread: QThread | None = None

        label = QLabel(message)
        bar = QProgressBar()
        bar.setRange(0, 0)  # indeterminate pulse

        layout = QVBoxLayout(self)
        layout.addWidget(label)
        layout.addWidget(bar)

        if cancellable:
            btn = QPushButton("Cancel")
            btn.clicked.connect(self._on_cancel)
            layout.addWidget(btn, 0, Qt.AlignmentFlag.AlignRight)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, fn: Callable[[], Any]) -> tuple[bool, Any, str | None]:
        """Run *fn* in a background thread; return ``(ok, result, error)``.

        * ``ok=True``: *result* holds the return value of *fn*.
        * ``ok=False, error=None``: user cancelled.
        * ``ok=False, error=str``: *fn* raised an exception.
        """
        self._worker = QThreadWorker(fn)
        self._thread = QThread()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._thread.start()
        self.exec()
        # Wait up to 3 s for the thread to exit cleanly (usually instant).
        if self._thread.isRunning():
            self._thread.wait(3000)
        if self._cancelled:
            return False, None, None
        if self._error is not None:
            return False, None, self._error
        return True, self._result, None

    # ------------------------------------------------------------------
    # Private slots
    # ------------------------------------------------------------------

    def _on_done(self, result: object) -> None:
        self._result = result
        self.accept()

    def _on_error(self, msg: str) -> None:
        self._error = msg
        self.reject()

    def _on_cancel(self) -> None:
        self._cancelled = True
        self.reject()


# ---------------------------------------------------------------------------
# Network-error helper
# ---------------------------------------------------------------------------

_NETWORK_KEYWORDS = (
    "connectionerror",
    "connecttimeout",
    "readtimeout",
    "timeout",
    "networkerror",
    "connection refused",
    "name or service not known",
    "nodename nor servname",
    "failed to establish",
    "remote end closed",
    "broken pipe",
    "errno 11001",  # getaddrinfo failed (Windows)
    "errno -2",  # name not resolved (Linux/macOS)
)


def is_network_error(error: str) -> bool:
    """Return True if *error* looks like a transient network/connectivity error."""
    lowered = error.lower()
    return any(kw in lowered for kw in _NETWORK_KEYWORDS)


def show_error_dialog(
    parent,
    title: str,
    error: str,
    *,
    extra_detail: str | None = None,
) -> None:
    """Show a QMessageBox for *error*, using a friendlier message for network errors."""
    from PySide6.QtWidgets import QMessageBox  # noqa: PLC0415

    if is_network_error(error):
        detail = (
            "Check your internet connection and try again.\n\n"
            + (extra_detail + "\n\n" if extra_detail else "")
            + f"Detail: {error}"
        )
        QMessageBox.warning(parent, "Connection problem", detail)
    else:
        msg = f"{extra_detail}\n\n{error}" if extra_detail else error
        QMessageBox.critical(parent, title, msg)
