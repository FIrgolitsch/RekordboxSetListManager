"""Unit tests for services.telemetry."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import patch

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
    """Redirect telemetry log dir to tmp_path and reset config each test."""
    monkeypatch.setattr(tel_mod, "_LOG_DIR", tmp_path)
    monkeypatch.setattr(tel_mod, "_LOG_PATH", tmp_path / "events.jsonl")
    monkeypatch.setattr(tel_mod, "_LOG_BACKUP", tmp_path / "events.1.jsonl")


# ---------------------------------------------------------------------------
# Opt-in gate
# ---------------------------------------------------------------------------

def test_record_does_nothing_when_disabled(tmp_path: Path) -> None:
    with patch("rekordbox_set_list_manager.services.telemetry.is_enabled", return_value=False):
        telemetry.record("app_start")
    log = tmp_path / "events.jsonl"
    assert not log.exists()


def test_record_writes_when_enabled(tmp_path: Path) -> None:
    with patch("rekordbox_set_list_manager.services.telemetry.is_enabled", return_value=True):
        telemetry.record("app_start", version="0.1.0")
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
    lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["event"] == "project_open"
    assert json.loads(lines[1])["event"] == "project_save"


# ---------------------------------------------------------------------------
# Log rotation
# ---------------------------------------------------------------------------

def test_log_rotates_when_file_exceeds_max_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tel_mod, "_MAX_LOG_BYTES", 10)  # tiny threshold
    log = tmp_path / "events.jsonl"
    log.write_text("x" * 20, encoding="utf-8")  # already over threshold

    with patch("rekordbox_set_list_manager.services.telemetry.is_enabled", return_value=True):
        telemetry.record("app_start")

    backup = tmp_path / "events.1.jsonl"
    assert backup.exists()
    assert backup.read_text(encoding="utf-8") == "x" * 20
    # new log contains only the new event
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["event"] == "app_start"


# ---------------------------------------------------------------------------
# Extra fields
# ---------------------------------------------------------------------------

def test_record_stores_extra_fields(tmp_path: Path) -> None:
    with patch("rekordbox_set_list_manager.services.telemetry.is_enabled", return_value=True):
        telemetry.record("import_finished", track_count=42)
    line = (tmp_path / "events.jsonl").read_text(encoding="utf-8").strip()
    event = json.loads(line)
    assert event["track_count"] == 42


# ---------------------------------------------------------------------------
# Remote sink — fire-and-forget; failures must not propagate
# ---------------------------------------------------------------------------

def test_remote_sink_does_not_raise_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SET_MANAGER_TELEMETRY_URL", "http://localhost:0/no-such-endpoint")
    with patch("rekordbox_set_list_manager.services.telemetry.is_enabled", return_value=True):
        # Should not raise even if the URL is unreachable.
        telemetry.record("app_start")
