"""Download queue controller — owns the background drain and signal surface."""

import dataclasses
import logging
import queue
import threading
import time
import uuid
from enum import StrEnum
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QThread, Signal

from rekordbox_set_list_manager.gui.widgets.common.download_worker import DownloadWorker
from rekordbox_set_list_manager.gui.widgets.streaming.auth_worker import (
    StreamingAuthWorker,
)
from rekordbox_set_list_manager.services.tidal_download import (
    DownloadErrorKind,
    RateGate,
    TidalDownloadError,
    TidalDownloadService,
    abort_aware_sleeper,
)
from rekordbox_set_list_manager.services.tidal_service import TidalService

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from rekordbox_set_list_manager.services.tidal_download import DownloadResult

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
    reauth_finished = Signal()  # the one-shot re-auth thread ended

    def __init__(
        self,
        service_factory: Callable[[], TidalDownloadService] | None = None,
        *,
        tidal_service: TidalService | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        # Controller-owned TidalService: it picks up the session file a
        # settings dialog already persisted, and silent re-auth rebinds its
        # session in place so the per-item factory sees the fresh object.
        self._tidal = tidal_service if tidal_service is not None else TidalService()
        self._queue: queue.Queue[QueueItem] = queue.Queue()
        self._shutdown = threading.Event()
        # Two pause owners share the worker-visible ``_paused`` event: the
        # user via pause()/resume(), and the worker itself on an auth
        # failure.  ``_user_paused`` records the user's intent separately so
        # a successful silent re-auth never clears a deliberate pause.
        self._paused = threading.Event()
        self._user_paused = threading.Event()
        self._abort_work = threading.Event()
        # One gate for the controller's lifetime — request spacing persists
        # across per-item service instances, and the abort-aware sleeper lets
        # cancel/shutdown interrupt a long Retry-After wait promptly.
        self._gate = RateGate(sleep=abort_aware_sleeper(self._abort_work))
        self._service_factory = (
            service_factory if service_factory is not None else self._default_service_factory
        )
        self._items: dict[str, QueueItem] = {}
        self._batches_done: set[str] = set()
        # Re-auth lifecycle state: the last item_failed kind (delivered ahead
        # of auth_required by queued-signal order) decides whether an auth
        # pause means "restore the session" or "the client config is bad".
        self._last_failure_kind: object | None = None
        self._reauth_running = False
        self._reauth_thread: QThread | None = None
        self._reauth_worker: StreamingAuthWorker | None = None
        self._worker = DownloadWorker(
            self._queue,
            service_factory=self._service_factory,
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

        Entries whose ``tidal_id`` is already QUEUED or RUNNING are skipped;
        terminal items may re-enqueue and mint a fresh item id.

        Parameters
        ----------
        entries : Iterable[tuple[str, str]]
            ``(tidal_id, label)`` pairs — one :class:`QueueItem` each.

        Returns
        -------
        list[str]
            Opaque item ids for the accepted entries — empty after shutdown.
        """
        if self._shutdown.is_set():
            return []
        batch_id = uuid.uuid4().hex
        # A record whose cancel_event is already set is guaranteed to
        # terminate — its state just lags behind the queued item_cancelled
        # signal — so it must not dedup-block a fresh enqueue of the same
        # track (the new request would otherwise be silently dropped while
        # the old record drains to CANCELLED).
        active = {
            record.tidal_id
            for record in self._items.values()
            if record.state in (QueueItemState.QUEUED, QueueItemState.RUNNING)
            and not record.cancel_event.is_set()
        }
        minted: list[str] = []
        for tidal_id, label in entries:
            tid = str(tidal_id)
            if tid in active:
                continue
            item_id = uuid.uuid4().hex
            record = QueueItem(
                item_id=item_id,
                tidal_id=tid,
                label=label,
                batch_id=batch_id,
                cancel_event=threading.Event(),
            )
            self._items[item_id] = record
            active.add(tid)
            self._queue.put(record)
            self.item_added.emit(item_id, label, batch_id)
            minted.append(item_id)
        return minted

    def cancel(self, item_id: str) -> None:
        """Cancel one item — queued items skip the service call; running abort.

        Parameters
        ----------
        item_id : str
            The opaque id returned by :meth:`enqueue`.
        """
        record = self._items.get(item_id)
        if record is None or record.state in _TERMINAL_STATES:
            return
        record.cancel_event.set()
        # The worker's own _current marker decides whether the running item
        # is this one — registry state lags by queued-signal delivery.
        self._worker.abort_if_current(item_id)

    def cancel_all(self) -> None:
        """Cancel every non-terminal item and unpause the queue."""
        for record in self._items.values():
            if record.state not in _TERMINAL_STATES:
                record.cancel_event.set()
        self._abort_work.set()
        self._paused.clear()
        self._user_paused.clear()

    def pause(self) -> None:
        """Pause the drain loop — the worker starts no further items."""
        self._user_paused.set()
        self._paused.set()

    def resume(self) -> None:
        """Resume the drain loop after :meth:`pause`."""
        self._user_paused.clear()
        self._paused.clear()

    def shutdown(self) -> None:
        """Stop the worker cooperatively and join it with a bounded wait.

        Idempotent: the flag-setting runs once; the direct ``quit()`` is
        required because the queued ``done`` → ``quit`` delivery cannot run
        while the GUI thread blocks inside ``wait()``.
        """
        if not self._shutdown.is_set():
            self._shutdown.set()
            self._abort_work.set()
            self._paused.clear()
            self._user_paused.clear()
            for record in self._items.values():
                if record.state not in _TERMINAL_STATES:
                    record.cancel_event.set()
            if self._reauth_worker is not None:
                self._reauth_worker.cancel()
        # A re-auth thread blocked inside the service call cannot process
        # quit() until it returns — the wait is deliberately shorter than
        # the worker's so shutdown stays responsive.
        if self._reauth_thread is not None and self._reauth_thread.isRunning():
            self._reauth_thread.quit()
            self._reauth_thread.wait(1000)
        if self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(3000)

    def is_draining(self) -> bool:
        """True while a shutdown is in progress and a thread still runs.

        Covers the re-auth thread as well as the download worker: a
        ``try_silent_authenticate`` blocked in network I/O outlives
        ``shutdown``'s bounded join, and the deferred close must keep the
        window alive until it ends rather than let the app exit with a
        thread in flight.
        """
        reauth_running = self._reauth_thread is not None and self._reauth_thread.isRunning()
        return self._shutdown.is_set() and (self._thread.isRunning() or reauth_running)

    def snapshot(self) -> tuple[QueueItem, ...]:
        """Return the insertion-ordered registry of frozen item records."""
        return tuple(self._items.values())

    def _on_worker_progress(self, item_id: str, frac: float) -> None:
        record = self._items.get(item_id)
        if record is None:
            return
        state = QueueItemState.RUNNING if record.state is QueueItemState.QUEUED else record.state
        self._items[item_id] = dataclasses.replace(record, state=state, progress=frac)
        self.item_progress.emit(item_id, frac)

    def _on_worker_finished(self, item_id: str, result: DownloadResult) -> None:
        record = self._items.get(item_id)
        if record is None:
            return
        record = dataclasses.replace(
            record,
            state=QueueItemState.FINISHED,
            progress=1.0,
            downgraded=result.downgraded,
        )
        self._items[item_id] = record
        self.item_finished.emit(item_id, result)
        self._check_batch_terminal(record.batch_id)

    def _on_worker_failed(self, item_id: str, kind: object, msg: str) -> None:
        record = self._items.get(item_id)
        if record is None:
            return
        # auth_required arrives right after this emission — remember the
        # kind so its handler can tell a stale session (AUTH) from a bad
        # client credential (INVALID_CLIENT).
        self._last_failure_kind = kind
        record = dataclasses.replace(
            record, state=QueueItemState.FAILED, error_kind=str(kind), error=msg
        )
        self._items[item_id] = record
        self.item_failed.emit(item_id, kind, msg)
        self._check_batch_terminal(record.batch_id)

    def _on_worker_cancelled(self, item_id: str) -> None:
        record = self._items.get(item_id)
        if record is None:
            return
        record = dataclasses.replace(record, state=QueueItemState.CANCELLED)
        self._items[item_id] = record
        self.item_cancelled.emit(item_id)
        self._check_batch_terminal(record.batch_id)

    def _check_batch_terminal(self, batch_id: str) -> None:
        if batch_id in self._batches_done:
            return
        states = [r.state for r in self._items.values() if r.batch_id == batch_id]
        if states and all(state in _TERMINAL_STATES for state in states):
            self._batches_done.add(batch_id)
            self.batch_finished.emit(batch_id)

    def _on_worker_drained(self) -> None:
        self.queue_drained.emit()

    def _default_service_factory(self) -> TidalDownloadService:
        """Build a download service from the current Tidal session.

        Consulted once per dequeued item so a session rebound by silent
        re-auth is picked up without reconstructing the controller.  A
        missing session raises the classified AUTH error — the worker's
        existing ``TidalDownloadError`` route turns it into
        ``item_failed`` + pause + ``auth_required`` with no special-casing.
        """
        session = self._tidal.session
        if session is None:
            raise TidalDownloadError(
                "No authenticated Tidal session",
                kind=DownloadErrorKind.AUTH,
            )
        return TidalDownloadService(session, gate=self._gate)

    def _on_auth_required(self) -> None:
        """Re-emit the signal, then act on the failure kind behind it.

        The worker always emits ``item_failed`` immediately before
        ``auth_required``, so ``_last_failure_kind`` is the classified cause
        by the time this slot runs.
        """
        self.auth_required.emit()
        if self._last_failure_kind is DownloadErrorKind.INVALID_CLIENT:
            # A rejected client credential is a configuration fault — a
            # fresh session cannot repair it, so flush the queue rather
            # than spinning a doomed re-auth worker.
            self.cancel_all()
            return
        self._start_silent_reauth()

    def _start_silent_reauth(self) -> None:
        """Spawn the silent re-auth worker on its own one-shot thread.

        One attempt at a time: a second ``auth_required`` while a re-auth
        is in flight (or after shutdown began) is ignored — the drain is
        already paused, so nothing is lost by waiting on the first try.
        """
        if self._reauth_running or self._shutdown.is_set():
            return
        self._reauth_running = True
        # Parentless on purpose: ``cancel()`` cannot interrupt the blocking
        # ``check_login`` call, so the thread can outlive shutdown's bounded
        # wait — and must therefore outlive the controller too.  Its own
        # ``finished`` → ``deleteLater`` wiring reaps it once it ends.
        thread = QThread()
        worker = StreamingAuthWorker(self._tidal, silent=True)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_reauth_finished)
        worker.silent_failed.connect(self._on_reauth_failed)
        worker.error.connect(self._on_reauth_failed)
        worker.finished.connect(thread.quit)
        worker.silent_failed.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_reauth_thread_finished)
        self._reauth_thread = thread
        self._reauth_worker = worker
        thread.start()

    def _on_reauth_finished(self, _username: str) -> None:
        """Unpause the drain — the next item's factory reads the new session.

        Only an auth pause is released: when the user deliberately paused
        the queue, a successful silent re-auth must not drain it.
        """
        if not self._user_paused.is_set():
            self.resume()

    def _on_reauth_failed(self, *_args: object) -> None:
        """Silent re-auth could not restore a session — flush the queue."""
        self.cancel_all()

    def _on_reauth_thread_finished(self) -> None:
        """Drop the one-shot thread/worker refs once the thread has ended."""
        self._reauth_running = False
        self._reauth_thread = None
        self._reauth_worker = None
        # Lets a deferred close re-check is_draining() now that the thread
        # refs are cleared — the download worker's finished signal alone
        # cannot cover a re-auth still running after it.
        self.reauth_finished.emit()
