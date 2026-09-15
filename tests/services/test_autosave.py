"""Tests for the autosave / crash-recovery service."""

from __future__ import annotations

import logging
from unittest.mock import Mock

import pytest

import rekordbox_set_list_manager.services.autosave as autosave_mod
from rekordbox_set_list_manager.models.project import Project
from rekordbox_set_list_manager.services.autosave import (
    autosave_mtime,
    clear_autosave,
    read_autosave,
    write_autosave,
)


@pytest.fixture(autouse=True)
def _patch_autosave_dir(tmp_path, monkeypatch):
    """Redirect all autosave I/O to a temporary directory."""
    monkeypatch.setattr(autosave_mod, "_AUTOSAVE_DIR", tmp_path)


def test_write_and_read_roundtrip():
    proj = Project(name="Roundtrip")
    write_autosave(proj)

    recovered = read_autosave(proj.id)

    assert recovered is not None
    assert recovered.id == proj.id
    assert recovered.name == proj.name


def test_read_autosave_absent_returns_none():
    proj = Project(name="NoFile")
    assert read_autosave(proj.id) is None


def test_clear_removes_file():
    proj = Project(name="Clear")
    write_autosave(proj)
    assert read_autosave(proj.id) is not None

    clear_autosave(proj.id)

    assert read_autosave(proj.id) is None


def test_mtime_nonzero_after_write():
    proj = Project(name="Mtime")
    write_autosave(proj)
    assert autosave_mtime(proj.id) > 0.0


def test_mtime_zero_when_absent():
    proj = Project(name="Absent")
    assert autosave_mtime(proj.id) == 0.0


def test_read_corrupt_file_returns_none(tmp_path):
    proj = Project(name="Corrupt")
    path = tmp_path / f"{proj.id}.setmgr"
    path.write_text("not valid json", encoding="utf-8")

    assert read_autosave(proj.id) is None


def test_write_autosave_failure_logs_warning(monkeypatch, caplog):
    monkeypatch.setattr(autosave_mod, "save_project", Mock(side_effect=OSError("disk full")))
    proj = Project(name="Fail")

    with caplog.at_level(logging.WARNING, logger=autosave_mod.__name__):
        write_autosave(proj)  # must not raise

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings
    assert warnings[0].exc_info is not None


def test_read_autosave_failure_logs_warning(tmp_path, monkeypatch, caplog):
    proj = Project(name="Fail")
    path = tmp_path / f"{proj.id}.setmgr"
    path.write_text("x", encoding="utf-8")  # must exist so read is attempted
    monkeypatch.setattr(autosave_mod, "load_project", Mock(side_effect=OSError("io error")))

    with caplog.at_level(logging.WARNING, logger=autosave_mod.__name__):
        assert read_autosave(proj.id) is None

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings
    assert warnings[0].exc_info is not None
