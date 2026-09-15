"""Generic background worker for streaming service authentication."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal

if TYPE_CHECKING:
    from rekordbox_set_list_manager.services.streaming_base import StreamingService


class StreamingAuthWorker(QObject):
    """Runs a streaming service auth flow in a background thread.

    ``link_ready`` is emitted for device-code flows (e.g. Tidal) where the
    service calls *link_callback* to display a login URL to the user.
    Spotify's browser-redirect flow never calls the callback, so the signal
    is simply never emitted for that service.

    In *silent* mode the worker calls ``try_silent_authenticate`` instead:
    ``finished`` on success, ``silent_failed`` when no cached session can be
    restored — never ``error``, so a silent reconnect can't pop a dialog.

    ``cancel()`` requests cooperative cancellation: services that support
    ``cancel_check`` abort promptly, and in every case the worker discards
    the result instead of emitting into a dialog that may be closing.
    """

    link_ready = Signal(str)  # device-code URL / message (optional)
    finished = Signal(str)  # display name on success
    error = Signal(str)  # error message on failure
    silent_failed = Signal()  # silent mode: no cached session to restore

    def __init__(self, service: StreamingService, silent: bool = False) -> None:
        """Wrap *service* auth flow for execution in a background thread."""
        super().__init__()
        self._service = service
        self._silent = silent
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        """Request cancellation; the auth flow aborts and results are dropped."""
        self._cancelled.set()

    def run(self) -> None:
        """Execute the auth flow in a background thread and emit the result."""
        if self._silent:
            self._run_silent()
            return
        try:
            name = self._service.authenticate(
                link_callback=self.link_ready.emit,
                cancel_check=self._cancelled.is_set,
            )
        except Exception as exc:  # noqa: BLE001
            if not self._cancelled.is_set():
                self.error.emit(str(exc))
            return
        if not self._cancelled.is_set():
            self.finished.emit(name)

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
