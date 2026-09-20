"""Download queue controller — owns the background drain and signal surface."""

import dataclasses
import logging
import queue
import threading
import time
from enum import StrEnum
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QThread, Signal

from rekordbox_set_list_manager.gui.widgets.common.download_worker import DownloadWorker

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from rekordbox_set_list_manager.services.tidal_download import (
        DownloadResult,
        TidalDownloadService,
    )

log = logging.getLogger(__name__)


class QueueItemState(StrEnum):
    """Lifecycle state of a queued download item."""

    QUEUED = "queued"
    RUNNING = "running"
    FINISHED = "finished"
    FAILED = "failed"
    CANCELLED = "cancelled"


_TERMINAL_STATES = frozenset(
    {QueueItemState.FINISHED, QueueItemState.FAILED, QueueItemState.CANCELLED}
)


@dataclasses.dataclass(frozen=True)
class QueueItem:
    """One queued download — identity, routing fields, and terminal-state record.

    The same frozen record flows through the work queue and lives in the
    controller's registry; state transitions produce replacement records via
    ``dataclasses.replace`` on the GUI thread only.

    Attributes
    ----------
    item_id : str
        Opaque per-item identifier minted by :meth:`DownloadController.enqueue`.
    tidal_id : str
        The Tidal track id to download.
    label : str
        Display label carried by the ``item_added`` signal.
    batch_id : str
        Shared identifier for all items minted by one ``enqueue`` call.
    cancel_event : threading.Event
        Per-item cooperative cancel flag — the only mutable member, shared
        with the worker thread by design.
    state : QueueItemState
        Current lifecycle state.
    progress : float
        Last emitted progress fraction in ``[0.0, 1.0]``.
    error_kind : str | None
        Classified failure kind once FAILED, else ``None``.
    error : str | None
        Sanitized failure message once FAILED, else ``None``.
    downgraded : bool
        ``True`` when the download was served below the requested quality.
    """

    item_id: str
    tidal_id: str
    label: str
    batch_id: str
    cancel_event: threading.Event
    state: QueueItemState = QueueItemState.QUEUED
    progress: float = 0.0
    error_kind: str | None = None
    error: str | None = None
    downgraded: bool = False


class DownloadController(QObject):
    """Background Tidal download queue with cancel, pause, and bounded shutdown.

    The controller lives on the GUI thread and owns an insertion-ordered
    registry of frozen :class:`QueueItem` records.  A parentless
    :class:`DownloadWorker` runs the drain loop on a dedicated ``QThread``;
    worker signals land on private slots that replace registry records and
    re-emit the public signal surface.
    """

    item_added = Signal(str, str, str)  # item_id, label, batch_id
    item_progress = Signal(str, float)
    item_finished = Signal(str, object)  # item_id, DownloadResult
    item_failed = Signal(str, object, str)  # item_id, DownloadErrorKind, message
    item_cancelled = Signal(str)
    queue_drained = Signal()
    auth_required = Signal()
    batch_finished = Signal(str)
    worker_finished = Signal()

    def __init__(
        self,
        service_factory: Callable[[], TidalDownloadService],
        *,
        monotonic: Callable[[], float] = time.monotonic,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._service_factory = service_factory
        self._queue: queue.Queue[QueueItem] = queue.Queue()
        self._shutdown = threading.Event()
        self._paused = threading.Event()
        self._abort_work = threading.Event()
        self._items: dict[str, QueueItem] = {}
        self._batches_done: set[str] = set()
        self._worker = DownloadWorker(
            self._queue,
            service_factory=service_factory,
            shutdown=self._shutdown,
            paused=self._paused,
            abort_work=self._abort_work,
            monotonic=monotonic,
        )
        self._thread = QThread(self)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.item_progress.connect(self._on_worker_progress)
        self._worker.item_finished.connect(self._on_worker_finished)
        self._worker.item_failed.connect(self._on_worker_failed)
        self._worker.item_cancelled.connect(self._on_worker_cancelled)
        self._worker.queue_drained.connect(self._on_worker_drained)
        self._worker.auth_required.connect(self._on_auth_required)
        self._worker.done.connect(self._thread.quit)
        self._thread.finished.connect(self.worker_finished.emit)
        self.destroyed.connect(self.shutdown)
        self._thread.start()

    def enqueue(self, entries: Iterable[tuple[str, str]]) -> list[str]:
        """Queue ``(tidal_id, label)`` pairs for background download.

        Parameters
        ----------
        entries : Iterable[tuple[str, str]]
            ``(tidal_id, label)`` pairs — one :class:`QueueItem` each.

        Returns
        -------
        list[str]
            Opaque item ids for the accepted entries.
        """
        if self._shutdown.is_set():
            return []
        return []

    def cancel(self, item_id: str) -> None:
        """Cancel one item — queued items skip the service call; running abort.

        Parameters
        ----------
        item_id : str
            The opaque id returned by :meth:`enqueue`.
        """

    def cancel_all(self) -> None:
        """Cancel every non-terminal item and unpause the queue."""

    def pause(self) -> None:
        """Pause the drain loop — the worker starts no further items."""
        self._paused.set()

    def resume(self) -> None:
        """Resume the drain loop after :meth:`pause`."""
        self._paused.clear()

    def shutdown(self) -> None:
        """Stop the worker cooperatively and join it with a bounded wait."""
        if not self._shutdown.is_set():
            self._shutdown.set()
            self._abort_work.set()
            self._paused.clear()
        if self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(3000)

    def is_draining(self) -> bool:
        """True while a shutdown is in progress and the worker still runs."""
        return self._shutdown.is_set() and self._thread.isRunning()

    def snapshot(self) -> tuple[QueueItem, ...]:
        """Return the insertion-ordered registry of frozen item records."""
        return tuple(self._items.values())

    def _on_worker_progress(self, item_id: str, frac: float) -> None:
        pass

    def _on_worker_finished(self, item_id: str, result: DownloadResult) -> None:
        pass

    def _on_worker_failed(self, item_id: str, kind: object, msg: str) -> None:
        pass

    def _on_worker_cancelled(self, item_id: str) -> None:
        pass

    def _on_worker_drained(self) -> None:
        pass

    def _on_auth_required(self) -> None:
        pass
