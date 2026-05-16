"""Anonymous opt-in telemetry — local structured event log with optional remote sink.

All telemetry is **opt-in, off by default**.  Nothing is written or sent until the
user enables "Send anonymous usage data" in Settings.

Local log
---------
Events are written as newline-delimited JSON to
``~/.cache/rekordbox_set_list_manager/events.jsonl``.  The file rotates (renamed to
``events.1.jsonl``) when it reaches ``_MAX_LOG_BYTES`` (5 MB).  Only one
rotation backup is kept.

Remote sink (optional)
-----------------------
If the environment variable ``SET_MANAGER_TELEMETRY_URL`` is set and non-empty,
batches of events are POSTed as ``application/x-ndjson`` to that URL in a
background thread.  Failures are silently ignored — telemetry must never affect
the main application.

Privacy
-------
No PII is collected.  File paths, playlist names, track titles, artist names,
and user identifiers are **never** recorded.
"""

from __future__ import annotations

import contextlib
import datetime
import json
import os
import threading
from pathlib import Path
from typing import Any

import platformdirs

from rekordbox_set_list_manager.utils.config import get as config_get

# ---------------------------------------------------------------------------
# Internal constants
# ---------------------------------------------------------------------------

_LOG_DIR = Path(platformdirs.user_cache_dir("rekordbox_set_list_manager"))
_LOG_PATH = _LOG_DIR / "events.jsonl"
_LOG_BACKUP = _LOG_DIR / "events.1.jsonl"
_MAX_LOG_BYTES = 5 * 1024 * 1024  # 5 MB

_CONFIG_KEY = "telemetry_enabled"


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
        Additional scalar metadata — counts, enum values, booleans.
        No strings containing user content (paths, names, etc.).

    """
    if not is_enabled():
        return
    payload: dict[str, Any] = {
        "event": event,
        "ts": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        **fields,
    }
    _write_local(payload)
    _maybe_send_remote(payload)


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


def _maybe_send_remote(payload: dict[str, Any]) -> None:
    url = os.environ.get("SET_MANAGER_TELEMETRY_URL", "").strip()
    if not url:
        return
    # Fire-and-forget in a daemon thread — never block the UI.
    t = threading.Thread(target=_post, args=(url, payload), daemon=True)
    t.start()


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
