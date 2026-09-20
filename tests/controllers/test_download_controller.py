"""Headless tests for the download queue controller/worker pair."""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from rekordbox_set_list_manager.controllers.download_controller import (
    DownloadController,
    QueueItem,
    QueueItemState,
)
from rekordbox_set_list_manager.gui.widgets.common.download_worker import (
    DownloadWorker,
)
from rekordbox_set_list_manager.services.tidal_download import (
    DownloadErrorKind,
    DownloadResult,
    TidalDownloadError,
)

_TERMINAL = {QueueItemState.FINISHED, QueueItemState.FAILED, QueueItemState.CANCELLED}


def _result(track_id: str) -> DownloadResult:
    return DownloadResult(
        filepath=Path(f"/fake/{track_id}.aiff"),
        track_id=track_id,
        served_quality="LOSSLESS",
        requested_quality="HI_RES_LOSSLESS",
        downgraded=False,
        codec="flac",
        duration_s=180.0,
    )


def _succeed(track_id, progress_cb, cancel_event):
    if progress_cb is not None:
        progress_cb(0.5)
    return _result(track_id)


def _wait_for_cancel(track_id, progress_cb, cancel_event):
    cancel_event.wait(10)
    raise TidalDownloadError("cancelled", kind=DownloadErrorKind.CANCELLED)


def _queue_record(item_id, tidal_id="t", batch_id="b"):
    return QueueItem(
        item_id=item_id,
        tidal_id=tidal_id,
        label="label",
        batch_id=batch_id,
        cancel_event=threading.Event(),
    )


class _FakeService:
    """Scriptable ``download_track`` stand-in asserting serial execution."""

    def __init__(self):
        self.calls: list[str] = []
        self.handlers: dict[str, Callable] = {}
        self.inflight = 0
        self.max_inflight = 0

    def download_track(self, track_id, *, progress_cb=None, cancel_event=None):
        self.calls.append(str(track_id))
        self.inflight += 1
        self.max_inflight = max(self.max_inflight, self.inflight)
        assert self.inflight == 1, "download_track re-entered — serial invariant broken"
        try:
            handler = self.handlers.get(str(track_id), _succeed)
            return handler(str(track_id), progress_cb, cancel_event)
        finally:
            self.inflight -= 1


class _Capture:
    """Collect every controller signal emission into ``(name, args)`` tuples."""

    def __init__(self, ctrl):
        self.events: list[tuple[str, tuple]] = []
        ctrl.item_added.connect(lambda *a: self.events.append(("added", a)))
        ctrl.item_progress.connect(lambda *a: self.events.append(("progress", a)))
        ctrl.item_finished.connect(lambda *a: self.events.append(("finished", a)))
        ctrl.item_failed.connect(lambda *a: self.events.append(("failed", a)))
        ctrl.item_cancelled.connect(lambda *a: self.events.append(("cancelled", a)))
        ctrl.queue_drained.connect(lambda: self.events.append(("drained", ())))
        ctrl.auth_required.connect(lambda: self.events.append(("auth", ())))
        ctrl.batch_finished.connect(lambda *a: self.events.append(("batch", a)))
        ctrl.worker_finished.connect(lambda: self.events.append(("worker_done", ())))

    def of(self, name, item_id=None):
        return [
            args
            for n, args in self.events
            if n == name and (item_id is None or (args and args[0] == item_id))
        ]


@pytest.fixture
def make_ctrl(qtbot):
    """Factory creating controllers; every instance is shut down at teardown."""
    created = []

    def _factory(**kwargs):
        fake = _FakeService()
        ctrl = DownloadController(service_factory=lambda: fake, **kwargs)
        created.append(ctrl)
        return ctrl, fake

    yield _factory

    for ctrl in created:
        ctrl.shutdown()


def test_happy_path_serial_with_bookend_progress(make_ctrl, qtbot):
    ctrl, fake = make_ctrl()
    cap = _Capture(ctrl)
    ids = ctrl.enqueue([("t1", "A — B"), ("t2", "C — D"), ("t3", "E — F")])
    assert len(ids) == 3
    assert len(set(ids)) == 3
    qtbot.waitUntil(lambda: len(cap.of("finished")) == 3, timeout=5000)

    added = cap.of("added")
    assert [a[0] for a in added] == ids
    assert [a[1] for a in added] == ["A — B", "C — D", "E — F"]
    assert len({a[2] for a in added}) == 1  # one batch id per enqueue call

    for iid in ids:
        fracs = [a[1] for a in cap.of("progress", iid)]
        assert fracs[0] == 0.0
        assert fracs[-1] == 1.0
        assert fracs == sorted(fracs)

    results = [a[1] for a in cap.of("finished")]
    assert all(isinstance(r, DownloadResult) for r in results)
    assert fake.calls == ["t1", "t2", "t3"]  # serial, in enqueue order
    assert fake.max_inflight == 1


def test_queue_drained_fires_once_per_busy_idle(make_ctrl, qtbot):
    ctrl, fake = make_ctrl()
    drained = []
    ctrl.queue_drained.connect(lambda: drained.append(True))
    qtbot.wait(300)
    assert drained == []  # never fires before the first enqueue
    ctrl.enqueue([("t1", "L1")])
    qtbot.waitUntil(lambda: drained == [True], timeout=5000)
    qtbot.wait(300)
    assert drained == [True]  # no refire while idle


def test_queued_cancel_skips_service_call(make_ctrl, qtbot):
    ctrl, fake = make_ctrl()
    cap = _Capture(ctrl)
    gate = threading.Event()

    def block(track_id, progress_cb, cancel_event):
        gate.wait(5)
        return _succeed(track_id, progress_cb, cancel_event)

    fake.handlers["t1"] = block
    ids = ctrl.enqueue([("t1", "L1"), ("t2", "L2")])
    qtbot.waitUntil(lambda: fake.calls == ["t1"], timeout=3000)
    ctrl.cancel(ids[1])
    gate.set()
    qtbot.waitUntil(lambda: len(cap.of("cancelled")) == 1, timeout=5000)
    assert cap.of("cancelled")[0][0] == ids[1]
    assert "t2" not in fake.calls
    qtbot.waitUntil(lambda: len(cap.of("finished")) == 1, timeout=5000)
    assert cap.of("finished")[0][0] == ids[0]


def test_running_cancel_emits_cancelled_not_failed(make_ctrl, qtbot):
    ctrl, fake = make_ctrl()
    cap = _Capture(ctrl)
    fake.handlers["t1"] = _wait_for_cancel
    ids = ctrl.enqueue([("t1", "L1")])
    qtbot.waitUntil(lambda: fake.calls == ["t1"], timeout=3000)
    ctrl.cancel(ids[0])
    qtbot.waitUntil(lambda: len(cap.of("cancelled")) == 1, timeout=5000)
    assert cap.of("cancelled")[0][0] == ids[0]
    assert cap.of("failed") == []
    assert cap.of("finished") == []


def test_cancel_all_flags_everything_and_unpauses(make_ctrl, qtbot):
    ctrl, fake = make_ctrl()
    cap = _Capture(ctrl)
    ctrl.pause()
    ids = ctrl.enqueue([("t1", "L1"), ("t2", "L2"), ("t3", "L3")])
    qtbot.wait(400)
    assert fake.calls == []  # paused — nothing may start
    ctrl.cancel_all()
    qtbot.waitUntil(lambda: len(cap.of("cancelled")) == 3, timeout=5000)
    assert fake.calls == []
    assert {a[0] for a in cap.of("cancelled")} == set(ids)


def test_cancel_queued_item_does_not_abort_running_item(make_ctrl, qtbot):
    ctrl, fake = make_ctrl()
    cap = _Capture(ctrl)
    release = threading.Event()
    started = threading.Event()

    def block(track_id, progress_cb, cancel_event):
        started.set()
        release.wait(5)
        return _succeed(track_id, progress_cb, cancel_event)

    fake.handlers["t1"] = block
    ids = ctrl.enqueue([("t1", "L1"), ("t2", "L2")])
    qtbot.waitUntil(started.is_set, timeout=3000)
    ctrl.cancel(ids[1])
    # The running item's gate sleeps must stay untouched — only the worker's
    # own _current marker may authorize an abort.
    assert ctrl._abort_work.is_set() is False
    release.set()
    qtbot.waitUntil(
        lambda: len(cap.of("cancelled")) == 1 and len(cap.of("finished")) == 1,
        timeout=5000,
    )
    assert cap.of("cancelled")[0][0] == ids[1]
    assert cap.of("finished")[0][0] == ids[0]


def test_shutdown_while_running_is_bounded_and_idempotent(make_ctrl, qtbot):
    ctrl, fake = make_ctrl()
    cap = _Capture(ctrl)
    fake.handlers["t1"] = _wait_for_cancel
    ctrl.enqueue([("t1", "L1"), ("t2", "L2")])
    qtbot.waitUntil(lambda: fake.calls == ["t1"], timeout=3000)
    start = time.monotonic()
    ctrl.shutdown()
    assert time.monotonic() - start < 3.0
    qtbot.waitUntil(lambda: cap.of("worker_done") != [], timeout=3000)
    assert ctrl._thread.isRunning() is False
    assert ctrl.is_draining() is False
    ctrl.shutdown()  # idempotent — must neither raise nor hang
    states = {r.item_id: r.state for r in ctrl.snapshot()}
    assert all(s in _TERMINAL for s in states.values())


def test_shutdown_idle_queue(make_ctrl, qtbot):
    ctrl, fake = make_ctrl()
    cap = _Capture(ctrl)
    ctrl.shutdown()
    assert ctrl._thread.isRunning() is False
    qtbot.waitUntil(lambda: cap.of("worker_done") != [], timeout=3000)
    assert ctrl.is_draining() is False
    assert fake.calls == []


def test_enqueue_after_shutdown_is_rejected(make_ctrl, qtbot):
    ctrl, fake = make_ctrl()
    ctrl.shutdown()
    added = []
    ctrl.item_added.connect(lambda *a: added.append(a))
    assert ctrl.enqueue([("t1", "L1")]) == []
    assert added == []
    assert fake.calls == []
    assert ctrl.snapshot() == ()


def test_abort_if_current_only_aborts_the_running_item(qapp):
    work_queue: queue.Queue = queue.Queue()
    shutdown, paused, abort = threading.Event(), threading.Event(), threading.Event()
    worker = DownloadWorker(
        work_queue,
        service_factory=lambda: _FakeService(),
        shutdown=shutdown,
        paused=paused,
        abort_work=abort,
    )
    worker.abort_if_current("i1")
    assert abort.is_set() is False  # nothing running yet
    worker._current = _queue_record("i1")
    worker.abort_if_current("other")
    assert abort.is_set() is False  # a stale id must not abort the runner
    worker.abort_if_current("i1")
    assert abort.is_set() is True


def test_unexpected_error_fails_item_and_queue_continues(make_ctrl, qtbot):
    ctrl, fake = make_ctrl()
    cap = _Capture(ctrl)

    def boom(track_id, progress_cb, cancel_event):
        raise ValueError("fetch https://example.tidal.com/seg?token=abc failed")

    fake.handlers["t1"] = boom
    ids = ctrl.enqueue([("t1", "L1"), ("t2", "L2")])
    qtbot.waitUntil(
        lambda: len(cap.of("failed")) == 1 and len(cap.of("finished")) == 1,
        timeout=5000,
    )
    failed = cap.of("failed")[0]
    assert failed[0] == ids[0]
    assert failed[1] == DownloadErrorKind.NETWORK
    assert "token=" not in failed[2]
    assert cap.of("finished")[0][0] == ids[1]


def test_classified_failure_emits_failed_and_continues(make_ctrl, qtbot):
    ctrl, fake = make_ctrl()
    cap = _Capture(ctrl)

    def rate_limited(track_id, progress_cb, cancel_event):
        raise TidalDownloadError("too many requests", kind=DownloadErrorKind.RATE_LIMIT)

    fake.handlers["t1"] = rate_limited
    ids = ctrl.enqueue([("t1", "L1"), ("t2", "L2")])
    qtbot.waitUntil(
        lambda: len(cap.of("failed")) == 1 and len(cap.of("finished")) == 1,
        timeout=5000,
    )
    failed = cap.of("failed")[0]
    assert failed[0] == ids[0]
    assert failed[1] == DownloadErrorKind.RATE_LIMIT
    assert failed[2] == "too many requests"
    assert cap.of("finished")[0][0] == ids[1]


def test_auth_failure_pauses_and_emits_auth_required(make_ctrl, qtbot):
    ctrl, fake = make_ctrl()
    cap = _Capture(ctrl)

    def auth_fail(track_id, progress_cb, cancel_event):
        raise TidalDownloadError("no session", kind=DownloadErrorKind.AUTH)

    fake.handlers["t1"] = auth_fail
    ids = ctrl.enqueue([("t1", "L1"), ("t2", "L2")])
    qtbot.waitUntil(lambda: len(cap.of("auth")) == 1, timeout=5000)
    assert cap.of("failed")[0][0] == ids[0]
    assert cap.of("failed")[0][1] == DownloadErrorKind.AUTH
    qtbot.wait(400)
    assert fake.calls == ["t1"]  # paused — the next item never started
    ctrl.resume()
    qtbot.waitUntil(lambda: len(cap.of("finished")) == 1, timeout=5000)
    assert fake.calls == ["t1", "t2"]


def test_pause_freezes_and_resume_continues(make_ctrl, qtbot):
    ctrl, fake = make_ctrl()
    cap = _Capture(ctrl)
    ctrl.pause()
    ctrl.enqueue([("t1", "L1"), ("t2", "L2")])
    qtbot.wait(400)
    assert fake.calls == []
    ctrl.resume()
    qtbot.waitUntil(lambda: len(cap.of("finished")) == 2, timeout=5000)
    assert fake.calls == ["t1", "t2"]


def test_snapshot_insertion_order_and_states(make_ctrl, qtbot):
    ctrl, fake = make_ctrl()
    gate = threading.Event()

    def block(track_id, progress_cb, cancel_event):
        gate.wait(5)
        return _succeed(track_id, progress_cb, cancel_event)

    fake.handlers["t1"] = block
    ids = ctrl.enqueue([("t1", "L1"), ("t2", "L2")])
    qtbot.waitUntil(
        lambda: ctrl.snapshot()[0].state is QueueItemState.RUNNING, timeout=3000
    )
    snap = ctrl.snapshot()
    assert [r.item_id for r in snap] == ids
    assert snap[0].state is QueueItemState.RUNNING
    assert snap[1].state is QueueItemState.QUEUED
    assert QueueItem.__dataclass_params__.frozen is True
    gate.set()
    qtbot.waitUntil(
        lambda: all(r.state in _TERMINAL for r in ctrl.snapshot()), timeout=5000
    )
    assert all(r.state is QueueItemState.FINISHED for r in ctrl.snapshot())


def test_dedup_queued_running_and_reenqueue_after_terminal(make_ctrl, qtbot):
    ctrl, fake = make_ctrl()
    cap = _Capture(ctrl)
    gate = threading.Event()

    def block(track_id, progress_cb, cancel_event):
        gate.wait(5)
        return _succeed(track_id, progress_cb, cancel_event)

    fake.handlers["t1"] = block
    ids = ctrl.enqueue([("t1", "L1")])
    qtbot.waitUntil(lambda: fake.calls == ["t1"], timeout=3000)
    assert ctrl.enqueue([("t1", "L1-dup")]) == []  # RUNNING dup → no-op
    gate.set()
    qtbot.waitUntil(lambda: len(cap.of("finished")) == 1, timeout=5000)
    ids2 = ctrl.enqueue([("t1", "L1-again")])
    assert len(ids2) == 1
    assert ids2[0] != ids[0]  # terminal → re-enqueue mints a new id
    qtbot.waitUntil(lambda: len(cap.of("finished")) == 2, timeout=5000)


def test_item_failed_message_strips_url_query(make_ctrl, qtbot):
    ctrl, fake = make_ctrl()
    cap = _Capture(ctrl)

    def fail_with_url(track_id, progress_cb, cancel_event):
        raise TidalDownloadError(
            "fetch failed https://example.tidal.com/seg/1?token=SECRET&x=1#frag",
            kind=DownloadErrorKind.NETWORK,
        )

    fake.handlers["t1"] = fail_with_url
    ctrl.enqueue([("t1", "L1")])
    qtbot.waitUntil(lambda: len(cap.of("failed")) == 1, timeout=5000)
    msg = cap.of("failed")[0][2]
    assert "https://example.tidal.com/seg/1" in msg
    assert "token=" not in msg
    assert "SECRET" not in msg
    assert "#frag" not in msg
