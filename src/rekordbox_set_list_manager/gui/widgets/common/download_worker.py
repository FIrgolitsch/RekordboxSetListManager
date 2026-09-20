"""Resident drain-loop worker for the download queue on a dedicated QThread."""

import logging
import queue
import re
import threading
import time
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal

from rekordbox_set_list_manager.services.tidal_download import (
    DownloadErrorKind,
    TidalDownloadError,
    _MergedEvent,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from rekordbox_set_list_manager.controllers.download_controller import QueueItem
    from rekordbox_set_list_manager.services.tidal_download import TidalDownloadService

log = logging.getLogger(__name__)

_POLL_SECONDS = 0.2

_URL_TAIL = re.compile(r"https?://[^\s?#]+[?#]\S*")


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
        now = self._now()
        if self.last_frac is None or frac >= 1.0:
            self.last_frac = frac
            self._last_t = now
            return frac
        if frac - self.last_frac >= self._delta and now - self._last_t >= self._interval:
            self.last_frac = frac
            self._last_t = now
            return frac
        return None


def _sanitize_message(msg: str) -> str:
    """Strip ``?…``/``#…`` tails from URLs in *msg* (pre-signed bearer URLs)."""
    return _URL_TAIL.sub(lambda m: m.group(0).split("?")[0].split("#")[0], msg)


class DownloadWorker(QObject):
    """Drain a ``queue.Queue`` on a dedicated QThread, one item at a time.

    The worker is parentless so :meth:`QObject.moveToThread` succeeds.  All
    control into the drain loop is ``threading.Event`` mutation — the events
    are shared with the owning controller; no queued slots are involved, so
    a ``run()`` that occupies the worker's event loop still observes cancel,
    pause, and shutdown promptly.
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
        self._throttle = _Throttle(monotonic=monotonic)
        self._current: QueueItem | None = None
        self._had_items = False

    def abort_if_current(self, item_id: str) -> None:
        """Set the shared abort event when the running item is *item_id*.

        A plain method invoked from the GUI thread — the registry's RUNNING
        flag lags behind this worker by queued-signal delivery, so consulting
        ``self._current`` is the only race-free way to abort a running item
        without touching the next item's gate sleeps.
        """
        current = self._current
        if current is not None and current.item_id == item_id:
            self._abort_work.set()

    def run(self) -> None:
        """Drain queued items until shutdown — the QThread entry point."""
        try:
            while not self._shutdown.is_set():
                if not self._wait_if_paused():
                    break
                try:
                    item = self._queue.get(timeout=_POLL_SECONDS)
                except queue.Empty:
                    if self._had_items:
                        self._had_items = False
                        self.queue_drained.emit()
                    continue
                if self._shutdown.is_set() or item.cancel_event.is_set():
                    self._had_items = True
                    self.item_cancelled.emit(item.item_id)
                    continue
                # A pause that landed while get() blocked must still gate this
                # item's service call — re-check after dequeue.
                if not self._wait_if_paused():
                    self._had_items = True
                    self.item_cancelled.emit(item.item_id)
                    continue
                self._run_item(item)
            # Tail-drain: shutdown must never strand a queued record.
            while True:
                try:
                    item = self._queue.get_nowait()
                except queue.Empty:
                    break
                self.item_cancelled.emit(item.item_id)
        finally:
            self.done.emit()

    def _wait_if_paused(self) -> bool:
        """Wait while paused; return ``False`` when shutdown interrupts."""
        while self._paused.is_set() and not self._shutdown.is_set():
            self._shutdown.wait(0.1)
        return not self._shutdown.is_set()

    def _throttled(self, item: QueueItem) -> Callable[[float], None]:
        """Progress callback feeding this item's throttle into ``item_progress``."""
        throttle = self._throttle

        def _cb(frac: float) -> None:
            emitted = throttle.feed(frac)
            if emitted is not None:
                self.item_progress.emit(item.item_id, emitted)

        return _cb

    def _run_item(self, item: QueueItem) -> None:
        """Download one queue item and emit its single terminal signal."""
        self._had_items = True
        self._abort_work.clear()
        if item.cancel_event.is_set():
            # A cancel that landed between dequeue and here must not start
            # the service call.
            self.item_cancelled.emit(item.item_id)
            return
        self.item_progress.emit(item.item_id, 0.0)
        self._throttle = _Throttle(monotonic=self._monotonic)
        self._current = item
        try:
            service = self._service_factory()
            result = service.download_track(
                item.tidal_id,
                progress_cb=self._throttled(item),
                cancel_event=_MergedEvent(
                    item.cancel_event, self._shutdown, self._abort_work
                ),
            )
        except TidalDownloadError as exc:
            self._handle_download_error(item, exc)
        except Exception as exc:  # broad catch is deliberate — one bad item
            # must never strand the rest of the queue.
            log.debug(
                "Download item %s raised unexpectedly", item.item_id, exc_info=True
            )
            self.item_failed.emit(
                item.item_id, DownloadErrorKind.NETWORK, _sanitize_message(str(exc))
            )
        else:
            if self._throttle.last_frac is None or self._throttle.last_frac < 1.0:
                self.item_progress.emit(item.item_id, 1.0)
            self.item_finished.emit(item.item_id, result)
        finally:
            self._current = None

    def _handle_download_error(self, item: QueueItem, exc: TidalDownloadError) -> None:
        """Emit the terminal signal for a classified *exc*."""
        if exc.kind is DownloadErrorKind.CANCELLED:
            self.item_cancelled.emit(item.item_id)
            return
        self.item_failed.emit(item.item_id, exc.kind, _sanitize_message(str(exc)))
        if exc.kind in (DownloadErrorKind.AUTH, DownloadErrorKind.INVALID_CLIENT):
            # Auth failures pause the drain — hammering an unauthorized
            # endpoint only burns the rate budget.
            self._paused.set()
            self.auth_required.emit()
