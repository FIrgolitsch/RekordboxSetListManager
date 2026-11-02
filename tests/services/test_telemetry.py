"""Unit tests for services.telemetry."""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import TYPE_CHECKING
from unittest.mock import Mock, patch

import pytest

import rekordbox_set_list_manager.services.telemetry as tel_mod
from rekordbox_set_list_manager.services import telemetry

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_telemetry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect telemetry log dir to tmp_path and reset module state each test."""
    monkeypatch.setattr(tel_mod, "_LOG_DIR", tmp_path)
    monkeypatch.setattr(tel_mod, "_LOG_PATH", tmp_path / "events.jsonl")
    monkeypatch.setattr(tel_mod, "_LOG_BACKUP", tmp_path / "events.1.jsonl")
    monkeypatch.setattr(tel_mod, "_HTTPS_WARNED", False)
    monkeypatch.delenv("SET_MANAGER_TELEMETRY_URL", raising=False)


def _wait_drained() -> None:
    """Block until the consumer has processed every event queued so far."""
    tel_mod._QUEUE.join()


def _record_enabled(event: str, **fields: object) -> None:
    with patch("rekordbox_set_list_manager.services.telemetry.is_enabled", return_value=True):
        telemetry.record(event, **fields)
    _wait_drained()


# ---------------------------------------------------------------------------
# Opt-in gate
# ---------------------------------------------------------------------------


def test_record_does_nothing_when_disabled(tmp_path: Path) -> None:
    with patch("rekordbox_set_list_manager.services.telemetry.is_enabled", return_value=False):
        telemetry.record("app_start")
    log = tmp_path / "events.jsonl"
    assert not log.exists()


def test_record_writes_when_enabled(tmp_path: Path) -> None:
    _record_enabled("app_start", version="0.1.0")
    log = tmp_path / "events.jsonl"
    assert log.exists()
    event = json.loads(log.read_text(encoding="utf-8").strip())
    assert event["event"] == "app_start"
    assert event["version"] == "0.1.0"
    assert "ts" in event


# ---------------------------------------------------------------------------
# Log content
# ---------------------------------------------------------------------------


def test_record_appends_multiple_events(tmp_path: Path) -> None:
    with patch("rekordbox_set_list_manager.services.telemetry.is_enabled", return_value=True):
        telemetry.record("project_open")
        telemetry.record("project_save")
    _wait_drained()
    lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["event"] == "project_open"
    assert json.loads(lines[1])["event"] == "project_save"


def test_record_with_no_fields_writes_event_only(tmp_path: Path) -> None:
    _record_enabled("project_open")
    event = json.loads((tmp_path / "events.jsonl").read_text(encoding="utf-8").strip())
    assert event["event"] == "project_open"
    assert set(event) == {"event", "ts"}


def test_record_with_non_ascii_value_does_not_raise(tmp_path: Path) -> None:
    _record_enabled("app_start", version="café-0.1.0")
    line = (tmp_path / "events.jsonl").read_text(encoding="utf-8").strip()
    assert json.loads(line)["version"] == "café-0.1.0"


# ---------------------------------------------------------------------------
# Log rotation
# ---------------------------------------------------------------------------


def test_log_rotates_when_file_exceeds_max_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tel_mod, "_MAX_LOG_BYTES", 10)  # tiny threshold
    log = tmp_path / "events.jsonl"
    log.write_text("x" * 20, encoding="utf-8")  # already over threshold

    _record_enabled("app_start")

    backup = tmp_path / "events.1.jsonl"
    assert backup.exists()
    assert backup.read_text(encoding="utf-8") == "x" * 20
    # new log contains only the new event
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["event"] == "app_start"


# ---------------------------------------------------------------------------
# Field allowlist
# ---------------------------------------------------------------------------


def test_record_stores_allowed_fields(tmp_path: Path) -> None:
    _record_enabled("import_finished", track_count=42, total=42, matched=40)
    event = json.loads((tmp_path / "events.jsonl").read_text(encoding="utf-8").strip())
    assert event["track_count"] == 42
    assert event["total"] == 42
    assert event["matched"] == 40


def test_record_admits_strategy_prefixed_fields(tmp_path: Path) -> None:
    _record_enabled("match_strategy_used", total=3, matched=2, strategy_exact=1, strategy_fuzzy=1)
    event = json.loads((tmp_path / "events.jsonl").read_text(encoding="utf-8").strip())
    assert event["strategy_exact"] == 1
    assert event["strategy_fuzzy"] == 1


def test_record_drops_fields_not_on_allowlist(tmp_path: Path) -> None:
    _record_enabled("app_start", version="0.1.0", playlist_name="Top Tracks", artist="DJ")
    raw = (tmp_path / "events.jsonl").read_text(encoding="utf-8").strip()
    event = json.loads(raw)
    assert event["version"] == "0.1.0"
    assert "playlist_name" not in event
    assert "artist" not in event
    assert "Top Tracks" not in raw


# ---------------------------------------------------------------------------
# Single consumer thread
# ---------------------------------------------------------------------------


def test_multiple_records_share_one_consumer() -> None:
    with patch("rekordbox_set_list_manager.services.telemetry.is_enabled", return_value=True):
        telemetry.record("project_open")
        first = tel_mod._CONSUMER
        telemetry.record("project_save")
        second = tel_mod._CONSUMER
    _wait_drained()
    assert first is not None
    assert second is first
    consumers = [
        t for t in threading.enumerate() if t.name == "telemetry-consumer" and t.is_alive()
    ]
    assert len(consumers) == 1


def test_record_does_not_spawn_thread_per_event() -> None:
    # Stop any consumer left running by an earlier test so exactly one fresh
    # consumer start can be observed here.
    tel_mod._flush(timeout=5.0)
    with (
        patch("rekordbox_set_list_manager.services.telemetry.is_enabled", return_value=True),
        patch.object(tel_mod.threading, "Thread", wraps=threading.Thread) as thread_spy,
    ):
        telemetry.record("project_open")
        telemetry.record("project_save")
        telemetry.record("project_open")
    _wait_drained()
    assert thread_spy.call_count == 1


# ---------------------------------------------------------------------------
# Remote sink — https only, fire-and-forget, failures must not propagate
# ---------------------------------------------------------------------------


def test_remote_sink_does_not_raise_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SET_MANAGER_TELEMETRY_URL", "https://localhost:0/no-such")
    # Should not raise even if the URL is unreachable.
    _record_enabled("app_start")
    assert (tmp_path / "events.jsonl").exists()


def test_http_remote_url_rejected_with_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("SET_MANAGER_TELEMETRY_URL", "http://telemetry.example.com")
    post = Mock()
    monkeypatch.setattr(tel_mod, "_post", post)

    with caplog.at_level(logging.WARNING, logger=tel_mod.__name__):
        _record_enabled("app_start")

    post.assert_not_called()
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    # Local log is still written.
    assert (tmp_path / "events.jsonl").exists()


def test_https_remote_url_is_posted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SET_MANAGER_TELEMETRY_URL", "https://telemetry.example.com/x")
    post = Mock()
    monkeypatch.setattr(tel_mod, "_post", post)

    _record_enabled("app_start", version="0.1.0")

    assert post.call_count == 1
    url, payload = post.call_args.args
    assert url == "https://telemetry.example.com/x"
    assert payload["event"] == "app_start"


def test_bad_scheme_warning_logged_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("SET_MANAGER_TELEMETRY_URL", "ftp://example.com")
    monkeypatch.setattr(tel_mod, "_post", Mock())
    with caplog.at_level(logging.WARNING, logger=tel_mod.__name__):
        _record_enabled("project_open")
        _record_enabled("project_save")
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1


# ---------------------------------------------------------------------------
# atexit flush — bounded drain via sentinel
# ---------------------------------------------------------------------------


def test_flush_drains_queue_and_stops_consumer(tmp_path: Path) -> None:
    _record_enabled("app_start", version="0.1.0")
    consumer = tel_mod._CONSUMER
    assert consumer is not None

    tel_mod._flush(timeout=5.0)

    assert not consumer.is_alive()
    assert tel_mod._QUEUE.unfinished_tasks == 0
    assert (tmp_path / "events.jsonl").exists()


def test_flush_without_consumer_is_noop() -> None:
    consumer = tel_mod._CONSUMER
    if consumer is not None and consumer.is_alive():
        pytest.skip("consumer still running from a prior test")
    tel_mod._flush(timeout=0.5)  # must not raise or block


def test_flush_join_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    def _slow_write(payload: dict) -> None:
        time.sleep(0.3)

    monkeypatch.setattr(tel_mod, "_write_local", _slow_write)
    with patch("rekordbox_set_list_manager.services.telemetry.is_enabled", return_value=True):
        telemetry.record("app_start")

    start = time.monotonic()
    tel_mod._flush(timeout=0.05)
    elapsed = time.monotonic() - start

    assert elapsed < 0.25  # join timed out rather than waiting for the slow write


def test_record_after_flush_restarts_consumer(tmp_path: Path) -> None:
    _record_enabled("project_open")
    tel_mod._flush(timeout=5.0)

    _record_enabled("project_save")

    lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2


def test_concurrent_record_and_flush_loses_no_events(tmp_path: Path) -> None:
    """Racing record() against repeated _flush() never strands or drops an event."""
    writers, per_writer = 4, 50
    stop = threading.Event()

    def _record_many() -> None:
        for _ in range(per_writer):
            telemetry.record("project_save")

    def _flush_repeatedly() -> None:
        while not stop.is_set():
            tel_mod._flush(timeout=5.0)

    with patch("rekordbox_set_list_manager.services.telemetry.is_enabled", return_value=True):
        flusher = threading.Thread(target=_flush_repeatedly)
        recorders = [threading.Thread(target=_record_many) for _ in range(writers)]
        flusher.start()
        for t in recorders:
            t.start()
        for t in recorders:
            t.join(timeout=30)
        stop.set()
        flusher.join(timeout=30)
    tel_mod._flush(timeout=5.0)

    lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == writers * per_writer
