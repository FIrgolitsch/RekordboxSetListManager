"""Tests for RekordboxDbService.import_files (db-mode download ingest)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from rekordbox_set_list_manager.services import rekordbox_db
from rekordbox_set_list_manager.services.rekordbox_db import (
    RekordboxDbError,
    RekordboxDbService,
)
from rekordbox_set_list_manager.services.skipped_item import SkippedItem

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _item(path, title="Song", isrc="USAA1"):
    """Build an ImportItem through the module so a missing class fails per-test."""
    return rekordbox_db.ImportItem(path=path, title=title, isrc=isrc)


def _make_db() -> MagicMock:
    """Return a mock Rekordbox6Database supporting the context manager protocol."""
    db = MagicMock()
    db.__enter__ = lambda self: self
    db.__exit__ = MagicMock(return_value=False)
    return db


@pytest.fixture(autouse=True)
def _rekordbox_not_running(monkeypatch):
    """Stub the process check so tests never depend on local machine state."""
    monkeypatch.setattr(
        "rekordbox_set_list_manager.services.rekordbox_db.get_rekordbox_pid",
        lambda *a, **k: 0,
        raising=False,
    )


# ---------------------------------------------------------------------------
# import_files — happy path
# ---------------------------------------------------------------------------


@patch("pyrekordbox.Rekordbox6Database")
def test_import_files_adds_content_and_commits_once(mock_cls, tmp_path):
    db = _make_db()
    content = MagicMock()
    content.ID = "42"
    db.add_content.return_value = content
    mock_cls.return_value = db

    path = tmp_path / "song.aiff"
    path.write_bytes(b"data")

    inserted, skipped = RekordboxDbService().import_files(
        [_item(path, title="Song A", isrc="USAA1")]
    )

    assert inserted == {str(path): 42}
    assert skipped == []
    db.add_content.assert_called_once_with(path, Title="Song A", ISRC="USAA1")
    db.commit.assert_called_once_with()
    db.engine.dispose.assert_called_once_with()


@patch("pyrekordbox.Rekordbox6Database")
def test_import_files_falls_back_to_stem_title_and_omits_blank_isrc(mock_cls, tmp_path):
    """An empty title falls back to the file stem; a missing ISRC is not passed."""
    db = _make_db()
    db.add_content.return_value = MagicMock(ID="9")
    mock_cls.return_value = db
    path = tmp_path / "stem-title.aiff"
    path.write_bytes(b"x")

    inserted, skipped = RekordboxDbService().import_files([_item(path, title="", isrc=None)])

    assert inserted == {str(path): 9}
    assert skipped == []
    db.add_content.assert_called_once_with(path, Title="stem-title")


# ---------------------------------------------------------------------------
# import_files — guards, skips, and the empty-set no-op
# ---------------------------------------------------------------------------


@patch("rekordbox_set_list_manager.services.rekordbox_db.get_rekordbox_pid")
@patch("rekordbox_set_list_manager.services.rekordbox_db._backup_db_files")
@patch("pyrekordbox.Rekordbox6Database")
def test_import_files_raises_when_rekordbox_running(mock_cls, mock_backup, mock_pid, tmp_path):
    mock_pid.return_value = 1234
    path = tmp_path / "song.aiff"
    path.write_bytes(b"data")

    with pytest.raises(RekordboxDbError, match="close it before importing"):
        RekordboxDbService().import_files([_item(path)])

    mock_backup.assert_not_called()
    mock_cls.assert_not_called()


@patch("rekordbox_set_list_manager.services.rekordbox_db.get_rekordbox_pid")
def test_export_set_to_db_keeps_exporting_guard_message(mock_pid):
    """The generalized write preflight must preserve the export message verbatim."""
    mock_pid.return_value = 1234

    with pytest.raises(RekordboxDbError, match="close it before exporting"):
        RekordboxDbService().export_set_to_db([], "My Set", {})


@patch("rekordbox_set_list_manager.services.rekordbox_db._backup_db_files")
@patch("pyrekordbox.Rekordbox6Database")
def test_import_files_empty_is_noop(mock_cls, mock_backup):
    """An empty ingest set never opens the database and never runs the backup."""
    inserted, skipped = RekordboxDbService().import_files([])

    assert inserted == {}
    assert skipped == []
    mock_cls.assert_not_called()
    mock_backup.assert_not_called()


@patch("pyrekordbox.Rekordbox6Database")
def test_import_files_skips_duplicate_folder_path(mock_cls, tmp_path):
    """A FolderPath dedup ValueError becomes a SkippedItem; the batch continues."""
    db = _make_db()
    existing = MagicMock()
    existing.ID = "7"
    db.add_content.side_effect = [existing, ValueError("FolderPath already exists")]
    mock_cls.return_value = db

    p1 = tmp_path / "one.aiff"
    p1.write_bytes(b"1")
    p2 = tmp_path / "two.aiff"
    p2.write_bytes(b"2")

    inserted, skipped = RekordboxDbService().import_files([_item(p1), _item(p2)])

    assert inserted == {str(p1): 7}
    assert len(skipped) == 1
    assert skipped[0].label == p2.name
    assert "not imported" in skipped[0].reason
    db.commit.assert_called_once_with()


@patch("pyrekordbox.Rekordbox6Database")
def test_import_files_skips_missing_file(mock_cls, tmp_path):
    """A path that does not exist is skipped before add_content is consulted."""
    db = _make_db()
    db.add_content.return_value = MagicMock(ID="5")
    mock_cls.return_value = db

    present = tmp_path / "present.aiff"
    present.write_bytes(b"x")
    missing = tmp_path / "missing.aiff"

    inserted, skipped = RekordboxDbService().import_files([_item(present), _item(missing)])

    assert inserted == {str(present): 5}
    assert skipped == [SkippedItem(missing.name, "file does not exist")]
    db.add_content.assert_called_once()  # only the existing file
    db.commit.assert_called_once_with()
