"""Anonymous opt-in telemetry — local structured event log with optional remote sink.

All telemetry is **opt-in, off by default**.  Nothing is written or sent until the
user enables "Send anonymous usage data" in Settings.

Local log
---------
Events are written as newline-delimited JSON to
``~/.cache/rekordbox_set_list_manager/events.jsonl``.  The file rotates (renamed to
``events.1.jsonl``) when it reaches ``_MAX_LOG_BYTES`` (5 MB).  Only one
rotation backup is kept.

Queue
-----
``record()`` enqueues each event onto the active consumer generation's
``queue.Queue``, drained by one lazily started daemon consumer thread —
never a thread per event.  An ``atexit`` hook enqueues a sentinel into the
current generation's private queue and joins the consumer with a bounded
timeout; any undelivered events are dropped rather than delaying
interpreter exit.  Telemetry must never affect the main application.

Remote sink (optional)
----------------------
If the environment variable ``SET_MANAGER_TELEMETRY_URL`` is set to a non-empty
**https** URL, events are POSTed as ``application/x-ndjson`` by the consumer
thread.  Non-https URLs are rejected with a logged warning — telemetry may only
leave the machine over TLS.

Privacy
-------
No PII is collected.  ``record()`` drops any field whose name is not on the
allowlist (``version``, ``track_count``, ``total``, ``matched``, and
``strategy_*``-prefixed names).  File paths, playlist names, track titles,
artist names, and user identifiers are **never** recorded.
"""

from __future__ import annotations

import atexit
import contextlib
import datetime
import json
import logging
import os
import queue
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import platformdirs

from rekordbox_set_list_manager.utils.config import get as config_get

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal constants
# ---------------------------------------------------------------------------

_LOG_DIR = Path(platformdirs.user_cache_dir("rekordbox_set_list_manager"))
_LOG_PATH = _LOG_DIR / "events.jsonl"
_LOG_BACKUP = _LOG_DIR / "events.1.jsonl"
_MAX_LOG_BYTES = 5 * 1024 * 1024  # 5 MB

_CONFIG_KEY = "telemetry_enabled"

# Field names observed at every record() call site, plus the strategy_ prefix
# used for per-strategy match counts.
_ALLOWED_FIELDS = frozenset({"version", "track_count", "total", "matched"})

_FLUSH_TIMEOUT = 2.0  # seconds — upper bound on exit-time drain


# ---------------------------------------------------------------------------
# Event queue + single consumer thread
# ---------------------------------------------------------------------------

class _Consumer:
    """One consumer generation: a private queue plus the thread draining it.

    The queue is per-generation so a shutdown sentinel can only ever be
    consumed by the generation it was issued for — a racing ``record()``
    that starts a replacement consumer can neither steal the sentinel nor
    strand events behind it.
    """

    def __init__(self) -> None:
        self.queue: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self.thread = threading.Thread(
            target=_drain,
            args=(self.queue,),
            name="telemetry-consumer",
            daemon=True,
        )
        self.thread.start()

    def is_alive(self) -> bool:
        """Return True while the consumer thread is running."""
        return self.thread.is_alive()


_QUEUE: queue.Queue[dict[str, Any] | None] = queue.Queue()  # active generation's queue
_CONSUMER: _Consumer | None = None
_CONSUMER_LOCK = threading.Lock()
_FLUSHED = False
_FLUSH_REGISTERED = False
_HTTPS_WARNED = False


def _ensure_consumer() -> _Consumer:
    global _CONSUMER, _FLUSHED, _FLUSH_REGISTERED, _QUEUE
    with _CONSUMER_LOCK:
        # A queued shutdown sentinel dooms the current consumer even while it
        # is still alive — anything enqueued after it would be stranded, so a
        # pending flush always means start a new generation.
        if _CONSUMER is None or not _CONSUMER.is_alive() or _FLUSHED:
            _CONSUMER = _Consumer()
            _QUEUE = _CONSUMER.queue
            _FLUSHED = False
        if not _FLUSH_REGISTERED:
            atexit.register(_flush)
            _FLUSH_REGISTERED = True
        return _CONSUMER


def _drain(q: queue.Queue[dict[str, Any] | None]) -> None:
    while True:
        payload = q.get()
        try:
            if payload is None:  # sentinel — clean shutdown
                return
            _write_local(payload)
            _maybe_send_remote(payload)
        finally:
            q.task_done()


def _flush(timeout: float = _FLUSH_TIMEOUT) -> None:
    """Enqueue the shutdown sentinel and join the consumer, bounded by *timeout*."""
    global _FLUSHED
    with _CONSUMER_LOCK:
        consumer = _CONSUMER
        # One sentinel per consumer generation: enqueue only while the consumer
        # is alive so no orphan sentinel can strand in the queue.
        if _FLUSHED or consumer is None or not consumer.is_alive():
            return
        _FLUSHED = True
        consumer.queue.put(None)
    consumer.thread.join(timeout=timeout)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def is_enabled() -> bool:
    """Return True if the user has opted in to telemetry."""
    return bool(config_get(_CONFIG_KEY, default=False))


def record(event: str, **fields: Any) -> None:  # noqa: ANN401
    """Record a telemetry event if opt-in is active.

    Parameters
    ----------
    event:
        Short snake_case event name, e.g. ``"app_start"``.
    **fields:
        Scalar metadata limited to the allowlist (``version``,
        ``track_count``, ``total``, ``matched``, ``strategy_*``); anything
        else is dropped.  No strings containing user content.

    """
    if not is_enabled():
        return
    safe_fields = {
        name: value
        for name, value in fields.items()
        if name in _ALLOWED_FIELDS or name.startswith("strategy_")
    }
    payload: dict[str, Any] = {
        "event": event,
        "ts": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        **safe_fields,
    }
    consumer = _ensure_consumer()
    consumer.queue.put_nowait(payload)


# ---------------------------------------------------------------------------
# Local logging
# ---------------------------------------------------------------------------

_write_lock = threading.Lock()


def _write_local(payload: dict[str, Any]) -> None:
    with _write_lock, contextlib.suppress(Exception):
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        if _LOG_PATH.exists() and _LOG_PATH.stat().st_size >= _MAX_LOG_BYTES:
            _LOG_BACKUP.unlink(missing_ok=True)
            _LOG_PATH.rename(_LOG_BACKUP)
        with _LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload) + "\n")


# ---------------------------------------------------------------------------
# Remote sink
# ---------------------------------------------------------------------------


def _remote_url() -> str | None:
    global _HTTPS_WARNED
    url = os.environ.get("SET_MANAGER_TELEMETRY_URL", "").strip()
    if not url:
        return None
    if urlparse(url).scheme != "https":
        if not _HTTPS_WARNED:
            _HTTPS_WARNED = True
            log.warning("Ignoring SET_MANAGER_TELEMETRY_URL: telemetry sink must use https")
        return None
    return url


def _maybe_send_remote(payload: dict[str, Any]) -> None:
    url = _remote_url()
    if url is not None:
        _post(url, payload)


def _post(url: str, payload: dict[str, Any]) -> None:
    with contextlib.suppress(Exception):
        import urllib.request  # noqa: PLC0415

        data = (json.dumps(payload) + "\n").encode()
        req = urllib.request.Request(  # noqa: S310
            url,
            data=data,
            headers={"Content-Type": "application/x-ndjson"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
            _ = resp.read()
