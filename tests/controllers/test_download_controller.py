"""Headless tests for the download queue controller/worker pair."""

from __future__ import annotations

import queue
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

import rekordbox_set_list_manager.utils.config as config_mod
from rekordbox_set_list_manager.controllers.download_controller import (
    DownloadController,
    QueueItem,
    QueueItemState,
)
from rekordbox_set_list_manager.gui.widgets.common.download_worker import (
    DownloadWorker,
    _sanitize_message,
    _Throttle,
)
from rekordbox_set_list_manager.services.tidal_download import (
    DownloadErrorKind,
    DownloadResult,
    RateGate,
    TidalDownloadError,
    TidalDownloadService,
    abort_aware_sleeper,
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


class _FakeTidalService:
    """TidalService stand-in for the controller's session + re-auth seams.

    ``try_silent_authenticate`` is scriptable: it counts calls, optionally
    blocks on ``auth_gate`` (to keep a re-auth attempt in flight), and
    returns ``auth_result``.  ``session`` is whatever the test installed.
    """

    def __init__(self, *, session=None, auth_result=None, block_auth=False):
        self.session = session
        self.auth_result = auth_result
        self.block_auth = block_auth
        self.auth_calls = 0
        self.auth_gate = threading.Event()

    def try_silent_authenticate(self):
        self.auth_calls += 1
        if self.block_auth:
            self.auth_gate.wait(10)
        return self.auth_result


def _fake_session():
    """Minimal session stand-in — only the ``config`` attrs the adapter needs."""
    return SimpleNamespace(
        config=SimpleNamespace(
            client_id="fake-client",
            client_secret="fake-secret",  # noqa: S106 — fixture value, not a real secret
        )
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


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path, monkeypatch):
    """Redirect config to a per-test dir and seed dummy client credentials.

    The controller's default factory constructs real ``TidalDownloadService``
    instances whose constructor applies resolved credentials — the seeded
    pair keeps that path hermetic against the developer's real config.
    """
    config_dir = tmp_path / "config"
    monkeypatch.setattr(config_mod, "_CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_mod, "_CONFIG_PATH", config_dir / "config.json")
    monkeypatch.setattr(config_mod, "_cache", None)
    monkeypatch.delenv("SET_MANAGER_TIDAL_CLIENT_ID", raising=False)
    monkeypatch.delenv("SET_MANAGER_TIDAL_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("SET_MANAGER_TIDAL_DOWNLOAD_WORKERS", raising=False)
    config_mod.set_value("tidal_client_id", "test-client-id")
    config_mod.set_value("tidal_client_secret", "test-client-secret")


@pytest.fixture
def make_ctrl(qtbot):
    """Factory creating controllers; every instance is shut down at teardown."""
    created = []

    def _factory(**kwargs):
        fake = _FakeService()
        kwargs.setdefault("service_factory", lambda: fake)
        ctrl = DownloadController(**kwargs)
        created.append(ctrl)
        return ctrl, fake

    yield _factory

    for ctrl in created:
        ctrl.shutdown()


@pytest.fixture
def make_default_ctrl(qtbot):
    """Controllers on the real default-factory path; shut down at teardown."""
    created = []

    def _factory(**kwargs):
        ctrl = DownloadController(**kwargs)
        created.append(ctrl)
        return ctrl

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
    ctrl, _fake = make_ctrl()
    drained = []
    ctrl.queue_drained.connect(lambda: drained.append(True))
    qtbot.wait(100)
    assert drained == []  # never fires before the first enqueue
    ctrl.enqueue([("t1", "L1")])
    qtbot.waitUntil(lambda: drained == [True], timeout=5000)
    qtbot.wait(100)
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
    qtbot.wait(100)
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
        service_factory=_FakeService,
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


def _auth_fail(track_id, progress_cb, cancel_event):
    raise TidalDownloadError("no session", kind=DownloadErrorKind.AUTH)


def test_auth_failure_pauses_and_emits_auth_required(qtbot):
    """AUTH failure: item_failed(AUTH) + pause-before-next-item +
    auth_required, then silent re-auth resumes the drain on a fresh factory
    call — the rebound session is picked up without a new controller."""
    tidal = _FakeTidalService(auth_result="DJ", block_auth=True)
    fake = _FakeService()
    factory_calls = []

    def _counting_factory():
        factory_calls.append(1)
        return fake

    ctrl = DownloadController(service_factory=_counting_factory, tidal_service=tidal)
    try:
        cap = _Capture(ctrl)

        fake.handlers["t1"] = _auth_fail
        ids = ctrl.enqueue([("t1", "L1"), ("t2", "L2")])
        qtbot.waitUntil(lambda: len(cap.of("auth")) == 1, timeout=5000)
        assert cap.of("failed")[0][0] == ids[0]
        assert cap.of("failed")[0][1] == DownloadErrorKind.AUTH
        # Silent re-auth starts on its own thread and the drain stays paused —
        # item 2 must not be dequeued while auth is in flight.
        qtbot.waitUntil(lambda: tidal.auth_calls == 1, timeout=5000)
        qtbot.wait(100)
        assert fake.calls == ["t1"]
        assert ctrl._worker._paused.is_set()
        assert len(factory_calls) == 1
        tidal.auth_gate.set()  # silent auth succeeds → controller resumes
        qtbot.waitUntil(lambda: len(cap.of("finished")) == 1, timeout=5000)
        assert fake.calls == ["t1", "t2"]
        assert len(factory_calls) == 2  # t2's service was minted post-re-auth
        qtbot.waitUntil(lambda: getattr(ctrl, "_reauth_thread", None) is None, timeout=3000)
    finally:
        tidal.auth_gate.set()
        ctrl.shutdown()


def test_silent_reauth_failure_cancels_remaining_items(make_ctrl, qtbot):
    """silent_failed/error → cancel_all: every queued item flushes through
    the drain loop as item_cancelled — nothing hangs on the pause."""
    tidal = _FakeTidalService(auth_result=None)  # silent auth finds nothing
    ctrl, fake = make_ctrl(tidal_service=tidal)
    cap = _Capture(ctrl)

    fake.handlers["t1"] = _auth_fail
    ids = ctrl.enqueue([("t1", "L1"), ("t2", "L2"), ("t3", "L3")])

    qtbot.waitUntil(lambda: len(cap.of("cancelled")) == 2, timeout=5000)
    assert tidal.auth_calls == 1
    assert {a[0] for a in cap.of("cancelled")} == {ids[1], ids[2]}
    failed = cap.of("failed", ids[0])
    assert len(failed) == 1
    assert failed[0][1] == DownloadErrorKind.AUTH
    assert cap.of("finished") == []
    assert fake.calls == ["t1"]
    qtbot.waitUntil(lambda: getattr(ctrl, "_reauth_thread", None) is None, timeout=3000)


def test_invalid_client_aborts_queue_without_silent_reauth(make_ctrl, qtbot):
    """INVALID_CLIENT is a configuration fault, not a stale session — the
    controller cancels the remaining items without attempting re-auth."""
    tidal = _FakeTidalService(auth_result="DJ")
    ctrl, fake = make_ctrl(tidal_service=tidal)
    cap = _Capture(ctrl)

    def bad_client(track_id, progress_cb, cancel_event):
        raise TidalDownloadError(
            "client credentials rejected", kind=DownloadErrorKind.INVALID_CLIENT
        )

    fake.handlers["t1"] = bad_client
    ids = ctrl.enqueue([("t1", "L1"), ("t2", "L2")])

    qtbot.waitUntil(lambda: len(cap.of("cancelled")) == 1, timeout=5000)
    assert cap.of("cancelled")[0][0] == ids[1]
    failed = cap.of("failed", ids[0])
    assert len(failed) == 1
    assert failed[0][1] == DownloadErrorKind.INVALID_CLIENT
    assert len(cap.of("auth")) == 1
    qtbot.wait(100)
    assert tidal.auth_calls == 0  # never a session fix for a config fault
    assert getattr(ctrl, "_reauth_thread", None) is None


def test_second_auth_required_during_reauth_does_not_respawn(make_ctrl, qtbot):
    """A second AUTH while silent re-auth is in flight must not spawn a
    second auth worker — the re-entrancy guard keeps it one-at-a-time."""
    tidal = _FakeTidalService(auth_result="DJ", block_auth=True)
    ctrl, fake = make_ctrl(tidal_service=tidal)

    fake.handlers["t1"] = _auth_fail
    ctrl.enqueue([("t1", "L1")])

    qtbot.waitUntil(lambda: tidal.auth_calls == 1, timeout=5000)
    ctrl._on_auth_required()  # second in-flight signal — must be ignored
    qtbot.wait(100)
    assert tidal.auth_calls == 1
    tidal.auth_gate.set()
    qtbot.waitUntil(lambda: getattr(ctrl, "_reauth_thread", None) is None, timeout=3000)


def test_shutdown_during_reauth_is_bounded_and_leaks_no_thread(make_ctrl, qtbot):
    """shutdown() mid-re-auth cancels the auth worker, bounded-joins its
    thread, and still drains + finishes the download worker."""
    tidal = _FakeTidalService(auth_result=None, block_auth=True)
    ctrl, fake = make_ctrl(tidal_service=tidal)
    cap = _Capture(ctrl)

    fake.handlers["t1"] = _auth_fail
    ctrl.enqueue([("t1", "L1"), ("t2", "L2")])

    qtbot.waitUntil(lambda: tidal.auth_calls == 1, timeout=5000)
    start = time.monotonic()
    ctrl.shutdown()
    assert time.monotonic() - start < 3.0
    qtbot.waitUntil(lambda: cap.of("worker_done") != [], timeout=3000)
    assert ctrl._thread.isRunning() is False
    states = {r.item_id: r.state for r in ctrl.snapshot()}
    assert all(s in _TERMINAL for s in states.values())
    tidal.auth_gate.set()  # let the cancelled silent auth return
    qtbot.waitUntil(
        lambda: getattr(ctrl, "_reauth_thread", None) is None and not ctrl._reauth_running,
        timeout=3000,
    )


def test_reauth_refs_release_after_deferred_delete(make_ctrl, qtbot):
    """Regression: the finished slot must not drop the last Python refs
    synchronously — the thread/worker deleteLater deliveries are still in
    flight, so a synchronous drop frees the C++ objects under a pending
    DeferredDelete (use-after-free on the GUI event loop).  Refs park in
    _reauth_stale_refs for exactly one event-loop turn, and a second re-auth
    cycle reuses the machinery without leftover wrappers."""
    tidal = _FakeTidalService(auth_result=None)  # silent auth finds nothing
    ctrl, fake = make_ctrl(tidal_service=tidal)

    fake.handlers["t1"] = _auth_fail
    fake.handlers["t2"] = _auth_fail
    ctrl.enqueue([("t1", "L1")])

    qtbot.waitUntil(lambda: tidal.auth_calls == 1, timeout=3000)
    qtbot.waitUntil(lambda: getattr(ctrl, "_reauth_thread", None) is None, timeout=3000)
    # The refs are gone from the controller but parked until the deferred
    # deletes land — the park drains on the next event-loop turn.
    qtbot.waitUntil(lambda: not ctrl._reauth_stale_refs, timeout=3000)

    # A second cycle exercises the stale-ref release at _start_silent_reauth.
    ctrl.enqueue([("t2", "L2")])
    qtbot.waitUntil(lambda: tidal.auth_calls == 2, timeout=3000)
    qtbot.waitUntil(lambda: getattr(ctrl, "_reauth_thread", None) is None, timeout=3000)
    qtbot.waitUntil(lambda: not ctrl._reauth_stale_refs, timeout=3000)


def test_pause_freezes_and_resume_continues(make_ctrl, qtbot):
    ctrl, fake = make_ctrl()
    cap = _Capture(ctrl)
    ctrl.pause()
    ctrl.enqueue([("t1", "L1"), ("t2", "L2")])
    qtbot.wait(100)
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
    qtbot.waitUntil(lambda: ctrl.snapshot()[0].state is QueueItemState.RUNNING, timeout=3000)
    snap = ctrl.snapshot()
    assert [r.item_id for r in snap] == ids
    assert snap[0].state is QueueItemState.RUNNING
    assert snap[1].state is QueueItemState.QUEUED
    assert QueueItem.__dataclass_params__.frozen is True
    gate.set()
    qtbot.waitUntil(lambda: all(r.state in _TERMINAL for r in ctrl.snapshot()), timeout=5000)
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


# --- Default service factory + shared abort-aware gate -----------------------


def test_default_factory_no_session_fails_auth_and_pauses(make_default_ctrl, qtbot):
    """Cold-start with no Tidal session: the factory's AUTH error drives the
    same item_failed + pause + auth_required path as a mid-queue expiry."""
    tidal = _FakeTidalService(session=None, block_auth=True)
    ctrl = make_default_ctrl(tidal_service=tidal)
    cap = _Capture(ctrl)

    ids = ctrl.enqueue([("t1", "L1"), ("t2", "L2")])

    qtbot.waitUntil(lambda: bool(cap.of("auth")), timeout=5000)
    qtbot.wait(100)
    failed = cap.of("failed", ids[0])
    assert len(failed) == 1
    assert failed[0][1] == DownloadErrorKind.AUTH
    assert ctrl._worker._paused.is_set()  # drain paused — t2 must not start
    tidal.auth_gate.set()  # release any in-flight silent re-auth
    qtbot.waitUntil(lambda: getattr(ctrl, "_reauth_thread", None) is None, timeout=3000)


def test_default_factory_builds_real_service_per_item_with_shared_gate(
    make_default_ctrl, qtbot, monkeypatch
):
    """The default factory mints a real TidalDownloadService per dequeued
    item, each carrying the controller's one shared rate gate."""
    built = []
    real_cls = TidalDownloadService

    def _recording_ctor(session, *, gate=None, **kwargs):
        svc = real_cls(session, gate=gate, **kwargs)
        built.append(svc)
        return svc

    monkeypatch.setattr(
        "rekordbox_set_list_manager.controllers.download_controller.TidalDownloadService",
        _recording_ctor,
    )
    monkeypatch.setattr(
        real_cls,
        "download_track",
        lambda self, track_id, *, progress_cb=None, cancel_event=None: _result(track_id),
    )
    session = _fake_session()
    tidal = _FakeTidalService(session=session)
    ctrl = make_default_ctrl(tidal_service=tidal)
    cap = _Capture(ctrl)

    ctrl.enqueue([("t1", "L1"), ("t2", "L2")])

    qtbot.waitUntil(lambda: len(built) == 2 or bool(cap.of("failed")), timeout=5000)
    assert len(built) == 2
    assert all(svc._session is session for svc in built)
    assert all(svc._gate is ctrl._gate for svc in built)
    qtbot.waitUntil(lambda: len(cap.of("finished")) == 2, timeout=5000)


def test_default_factory_reads_current_session_each_call(make_default_ctrl, qtbot, monkeypatch):
    """A session rebound between items (e.g. by silent re-auth) is picked up
    by the very next factory call — no controller reconstruction needed."""
    sessions_seen = []

    class _RecordingService:
        def __init__(self, session, *, gate=None):
            sessions_seen.append(session)

        def download_track(self, track_id, *, progress_cb=None, cancel_event=None):
            return _result(track_id)

    monkeypatch.setattr(
        "rekordbox_set_list_manager.controllers.download_controller.TidalDownloadService",
        _RecordingService,
    )
    first, second = _fake_session(), _fake_session()
    tidal = _FakeTidalService(session=first)
    ctrl = make_default_ctrl(tidal_service=tidal)
    cap = _Capture(ctrl)

    ctrl.enqueue([("t1", "L1")])
    qtbot.waitUntil(
        lambda: len(cap.of("finished")) == 1 or bool(cap.of("failed")),
        timeout=5000,
    )
    assert len(cap.of("finished")) == 1

    tidal.session = second  # rebind — next item must see the new object
    ctrl.enqueue([("t2", "L2")])
    qtbot.waitUntil(
        lambda: len(cap.of("finished")) == 2 or bool(cap.of("failed")),
        timeout=5000,
    )

    assert sessions_seen == [first, second]


def test_abort_aware_sleeper_raises_out_of_gate_acquire():
    """A set abort must raise out of acquire() — a sleeper that merely
    early-returns still lets the caller fire one more request."""
    abort = threading.Event()
    gate = RateGate(sleep=abort_aware_sleeper(abort))

    gate.acquire()  # stamps the baseline release; no wait on first call
    abort.set()

    with pytest.raises(InterruptedError):
        gate.acquire()


def test_controller_gate_sleeper_aborts_when_abort_work_set(make_ctrl):
    """The controller's shared gate sleeps through _abort_work, so a 300 s
    Retry-After wait is interrupted by cancel/shutdown."""
    ctrl, _fake = make_ctrl()

    ctrl._gate.acquire()  # baseline release stamp
    ctrl._abort_work.set()

    with pytest.raises(InterruptedError):
        ctrl._gate.acquire()


# --- Invariant battery -------------------------------------------------------


class _FakeClock:
    """Deterministic monotonic clock — ``advance`` moves time forward."""

    def __init__(self, start=100.0):
        self.now = start

    def advance(self, seconds):
        self.now += seconds

    def monotonic(self):
        return self.now


def test_throttle_first_call_and_terminal_always_emit():
    clock = _FakeClock()
    throttle = _Throttle(monotonic=clock.monotonic)
    assert throttle.feed(0.0) == 0.0  # first call always emits
    assert throttle.feed(1.0) == 1.0  # terminal always emits


def test_throttle_gates_on_delta_and_interval():
    clock = _FakeClock()
    throttle = _Throttle(monotonic=clock.monotonic)
    assert throttle.feed(0.0) == 0.0
    clock.advance(0.05)
    assert throttle.feed(0.02) is None  # delta ok, interval not elapsed
    clock.advance(0.1)
    assert throttle.feed(0.03) == 0.03  # both gates pass (0.15 s elapsed)
    assert throttle.feed(0.5) is None  # interval resets at the last emit
    clock.advance(0.125)
    assert throttle.feed(0.51) == 0.51
    assert throttle.feed(0.52) is None  # fresh emit resets the interval gate
    assert throttle.feed(1.0) == 1.0


def test_throttle_delta_measures_from_last_emit_not_last_feed():
    clock = _FakeClock()
    throttle = _Throttle(monotonic=clock.monotonic)
    assert throttle.feed(0.0) == 0.0
    clock.advance(0.2)
    assert throttle.feed(0.005) is None  # interval elapsed, delta too small
    clock.advance(0.2)
    assert throttle.feed(0.009) is None  # suppressed feeds don't rebase delta
    clock.advance(0.2)
    assert throttle.feed(0.011) == 0.011


def test_throttle_emissions_stay_within_bounds():
    throttle = _Throttle()
    out = [throttle.feed(f) for f in (0.0, 0.4, 0.7, 1.0)]
    emitted = [f for f in out if f is not None]
    assert all(0.0 <= f <= 1.0 for f in emitted)


def test_progress_burst_is_coalesced(make_ctrl, qtbot):
    ctrl, fake = make_ctrl()
    fracs = []
    finished = []
    ctrl.item_progress.connect(lambda _iid, frac: fracs.append(frac))
    ctrl.item_finished.connect(lambda *a: finished.append(a))

    def burst(track_id, progress_cb, cancel_event):
        for i in range(1, 51):
            progress_cb(i / 50)
        return _result(track_id)

    fake.handlers["t1"] = burst
    ctrl.enqueue([("t1", "L1")])
    qtbot.waitUntil(lambda: len(finished) == 1, timeout=5000)
    assert fracs[0] == 0.0
    assert fracs[-1] == 1.0
    assert fracs == sorted(fracs)
    # The 50-call burst runs in microseconds at real-clock speed, so the
    # interval gate suppresses nearly everything — the bound sits far under
    # the burst count but well above the observed ~3 emissions.
    assert len(fracs) <= 15


def test_queue_drained_refires_for_each_busy_cycle(make_ctrl, qtbot):
    ctrl, _fake = make_ctrl()
    drained = []
    ctrl.queue_drained.connect(lambda: drained.append(True))
    ctrl.enqueue([("t1", "L1")])
    qtbot.waitUntil(lambda: len(drained) == 1, timeout=5000)
    ctrl.enqueue([("t2", "L2")])
    qtbot.waitUntil(lambda: len(drained) == 2, timeout=5000)


def test_batch_finished_emits_once_per_enqueue_in_order(make_ctrl, qtbot):
    ctrl, _fake = make_ctrl()
    cap = _Capture(ctrl)
    ctrl.enqueue([("t1", "L1"), ("t2", "L2")])
    ctrl.enqueue([("t3", "L3")])
    qtbot.waitUntil(lambda: len(cap.of("batch")) == 2, timeout=5000)
    added = cap.of("added")
    batch1, batch2 = added[0][2], added[-1][2]
    assert batch1 != batch2
    assert [a[2] for a in added[:2]] == [batch1, batch1]
    assert added[2][2] == batch2
    assert [a[0] for a in cap.of("batch")] == [batch1, batch2]


def test_terminal_completeness_under_mixed_ops(make_ctrl, qtbot):
    ctrl, fake = make_ctrl()
    cap = _Capture(ctrl)
    gate = threading.Event()

    def block_then_succeed(track_id, progress_cb, cancel_event):
        gate.wait(5)
        if cancel_event.is_set():
            raise TidalDownloadError("cancelled", kind=DownloadErrorKind.CANCELLED)
        return _succeed(track_id, progress_cb, cancel_event)

    fake.handlers["t1"] = block_then_succeed
    ids1 = ctrl.enqueue([("t1", "L1"), ("t2", "L2"), ("t3", "L3")])
    qtbot.waitUntil(lambda: fake.calls == ["t1"], timeout=3000)
    ctrl.cancel(ids1[2])  # cancel a queued item behind the runner
    gate.set()
    ctrl.pause()
    ctrl.resume()  # pause+resume mid-sequence
    ids2 = ctrl.enqueue([("t4", "L4")])  # interleave a second batch
    ctrl.shutdown()  # shut down mid-drain
    qtbot.waitUntil(lambda: cap.of("worker_done") != [], timeout=3000)

    snap = ctrl.snapshot()
    assert len(snap) == 4
    assert all(r.state in _TERMINAL for r in snap)

    terminal_counts: dict[str, int] = {}
    for name in ("finished", "failed", "cancelled"):
        for args in cap.of(name):
            terminal_counts[args[0]] = terminal_counts.get(args[0], 0) + 1
    assert set(terminal_counts) == set(ids1 + ids2)
    assert all(count == 1 for count in terminal_counts.values())

    # An enqueue issued after shutdown is rejected rather than stranding
    # a record that could never reach a terminal state.
    assert ctrl.enqueue([("t5", "L5")]) == []
    assert len(ctrl.snapshot()) == 4


def test_sanitize_message_strips_url_query_and_fragment():
    msg = "fetch https://example.tidal.com/seg?token=SECRET#frag failed"
    clean = _sanitize_message(msg)
    assert "https://example.tidal.com/seg" in clean
    assert "token=" not in clean
    assert "SECRET" not in clean
    assert "#frag" not in clean
