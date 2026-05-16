"""Generic background worker for streaming service authentication."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal

if TYPE_CHECKING:
    from rekordbox_set_list_manager.services.streaming_base import StreamingService


class StreamingAuthWorker(QObject):
    """Runs :meth:`StreamingService.authenticate` in a background thread.

    ``link_ready`` is emitted for device-code flows (e.g. Tidal) where the
    service calls *link_callback* to display a login URL to the user.
    Spotify's browser-redirect flow never calls the callback, so the signal
    is simply never emitted for that service.
    """

    link_ready = Signal(str)  # device-code URL / message (optional)
    finished = Signal(str)  # display name on success
    error = Signal(str)  # error message on failure

    def __init__(self, service: StreamingService) -> None:
        """Wrap *service* auth flow for execution in a background thread."""
        super().__init__()
        self._service = service

    def run(self) -> None:
        """Execute the auth flow in a background thread and emit the result."""
        try:
            name = self._service.authenticate(link_callback=self.link_ready.emit)
            self.finished.emit(name)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))
