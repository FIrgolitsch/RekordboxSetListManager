"""Generic background worker for streaming service authentication."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal

if TYPE_CHECKING:
    from rekordbox_set_list_manager.services.streaming_base import StreamingService


class StreamingAuthWorker(QObject):
    """Runs a streaming service auth flow in a background thread.

    ``link_ready`` is emitted for link-based flows (e.g. Tidal) where the
    service calls *link_callback* to display a login URL to the user.
    ``redirect_requested`` is emitted when the flow additionally needs the
    browser redirect URL pasted back (Tidal PKCE) — the GUI should show a
    paste field and call :meth:`provide_redirect` with the entered URL.
    Spotify's browser-redirect flow emits neither signal.

    In *silent* mode the worker calls ``try_silent_authenticate`` instead:
    ``finished`` on success, ``silent_failed`` when no cached session can be
    restored — never ``error``, so a silent reconnect can't pop a dialog.

    ``cancel()`` requests cooperative cancellation: services that support
    ``cancel_check`` abort promptly, and in every case the worker discards
    the result instead of emitting into a dialog that may be closing.
    """

    link_ready = Signal(str)  # login URL / message (optional)
    redirect_requested = Signal()  # PKCE flow needs the pasted redirect URL
    finished = Signal(str)  # display name on success
    error = Signal(str)  # error message on failure
    silent_failed = Signal()  # silent mode: no cached session to restore

    _REDIRECT_POLL_SECONDS = 0.2

    def __init__(self, service: StreamingService, silent: bool = False) -> None:
        """Wrap *service* auth flow for execution in a background thread."""
        super().__init__()
        self._service = service
        self._silent = silent
        self._cancelled = threading.Event()
        self._redirect_ready = threading.Event()
        self._redirect_url: str | None = None

    def cancel(self) -> None:
        """Request cancellation; the auth flow aborts and results are dropped."""
        self._cancelled.set()

    def provide_redirect(self, url: str) -> None:
        """Deliver the pasted redirect URL (call from the GUI thread)."""
        self._redirect_url = url
        self._redirect_ready.set()

    def run(self) -> None:
        """Execute the auth flow in a background thread and emit the result."""
        if self._silent:
            self._run_silent()
            return
        try:
            name = self._service.authenticate(
                link_callback=self.link_ready.emit,
                cancel_check=self._cancelled.is_set,
                redirect_callback=self._wait_for_redirect,
            )
        except Exception as exc:  # noqa: BLE001
            if not self._cancelled.is_set():
                self.error.emit(str(exc))
            return
        if not self._cancelled.is_set():
            self.finished.emit(name)

    def _wait_for_redirect(self) -> str:
        """Block until the GUI supplies the redirect URL or cancel fires."""
        self.redirect_requested.emit()
        while not self._redirect_ready.wait(self._REDIRECT_POLL_SECONDS):
            if self._cancelled.is_set():
                raise InterruptedError("authentication cancelled")
        return self._redirect_url or ""

    def _run_silent(self) -> None:
        try:
            name = self._service.try_silent_authenticate()
        except Exception:  # noqa: BLE001 — silent reconnect never surfaces errors
            name = None
        if self._cancelled.is_set():
            return
        if name:
            self.finished.emit(name)
        else:
            self.silent_failed.emit()
