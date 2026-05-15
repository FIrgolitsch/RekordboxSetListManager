"""Tests for the autosave / crash-recovery service."""

from __future__ import annotations

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
