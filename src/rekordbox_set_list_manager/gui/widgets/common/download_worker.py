"""Resident drain-loop worker for the download queue on a dedicated QThread."""

import logging
import queue
import threading
import time
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal

if TYPE_CHECKING:
    from collections.abc import Callable

    from rekordbox_set_list_manager.controllers.download_controller import QueueItem
    from rekordbox_set_list_manager.services.tidal_download import TidalDownloadService

log = logging.getLogger(__name__)

_POLL_SECONDS = 0.2


class _Throttle:
    """Hybrid progress throttle — a fraction emits only when delta AND interval pass.

    The first call and any ``frac >= 1.0`` always emit; every other call must
    be at least *delta* above the last emitted fraction AND at least
    *interval* seconds after it.  ``monotonic`` is injectable so tests can
    drive the interval gate deterministically.
    """

    def __init__(
        self,
        *,
        delta: float = 0.01,
        interval: float = 0.125,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._delta = delta
        self._interval = interval
        self._now = monotonic
        self.last_frac: float | None = None
        self._last_t = 0.0

    def feed(self, frac: float) -> float | None:
        """Return *frac* when it may be emitted, else ``None`` to suppress it."""
        return None  # scaffold — gated emission lands with the drain loop


def _sanitize_message(msg: str) -> str:
    """Strip ``?…``/``#…`` tails from URLs in *msg* (pre-signed bearer URLs)."""
    return msg  # scaffold — the URL-tail strip lands with the drain loop


class DownloadWorker(QObject):
    """Drain a ``queue.Queue`` on a dedicated QThread, one item at a time.

    The worker is parentless so :meth:`QObject.moveToThread` succeeds.  All
    control into the drain loop is ``threading.Event`` mutation — the events
    are shared with the owning controller; no queued slots are involved, so
    signals from the GUI thread still reach a loop occupied by ``run()``.
    """

    item_progress = Signal(str, float)
    item_finished = Signal(str, object)
    item_failed = Signal(str, object, str)
    item_cancelled = Signal(str)
    queue_drained = Signal()
    auth_required = Signal()
    done = Signal()  # transport-only: fires once when run() returns

    def __init__(
        self,
        work_queue: queue.Queue[QueueItem],
        *,
        service_factory: Callable[[], TidalDownloadService],
        shutdown: threading.Event,
        paused: threading.Event,
        abort_work: threading.Event,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__()
        self._queue = work_queue
        self._service_factory = service_factory
        self._shutdown = shutdown
        self._paused = paused
        self._abort_work = abort_work
        self._monotonic = monotonic
        self._current: QueueItem | None = None
        self._had_items = False

    def abort_if_current(self, item_id: str) -> None:
        """Set the shared abort event when the running item is *item_id*."""

    def run(self) -> None:
        """Drain queued items until shutdown — the QThread entry point."""
        self.done.emit()
