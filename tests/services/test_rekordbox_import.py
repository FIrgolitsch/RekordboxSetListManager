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


@patch("pyrekordbox.Rekordbox6Database")
def test_import_files_dedup_skip_is_per_file(mock_cls, tmp_path):
    """A ValueError on item 2 of 3 skips only that item; 1 and 3 still land."""
    db = _make_db()
    db.add_content.side_effect = [
        MagicMock(ID="11"),
        ValueError("already exists"),
        MagicMock(ID="33"),
    ]
    mock_cls.return_value = db

    paths = []
    for name in ("one.aiff", "two.aiff", "three.aiff"):
        p = tmp_path / name
        p.write_bytes(b"x")
        paths.append(p)

    inserted, skipped = RekordboxDbService().import_files([_item(p) for p in paths])

    assert inserted == {str(paths[0]): 11, str(paths[2]): 33}
    assert len(skipped) == 1
    assert skipped[0].label == paths[1].name
    assert "not imported" in skipped[0].reason
    db.commit.assert_called_once_with()


@patch("pyrekordbox.Rekordbox6Database")
def test_import_files_hard_failure_aborts_batch_without_commit(mock_cls, tmp_path):
    """A non-ValueError mid-batch aborts: nothing commits, engine still freed."""
    db = _make_db()
    db.add_content.side_effect = [MagicMock(ID="1"), RuntimeError("disk full")]
    mock_cls.return_value = db

    p1 = tmp_path / "one.aiff"
    p1.write_bytes(b"1")
    p2 = tmp_path / "two.aiff"
    p2.write_bytes(b"2")

    with pytest.raises(RekordboxDbError, match="Failed to import files"):
        RekordboxDbService().import_files([_item(p1), _item(p2)])

    db.commit.assert_not_called()
    db.engine.dispose.assert_called_once_with()


# ---------------------------------------------------------------------------
# Write pre-flight ordering — the backup must precede the database open
# ---------------------------------------------------------------------------


@patch("rekordbox_set_list_manager.services.rekordbox_db._backup_db_files")
@patch("rekordbox_set_list_manager.services._pyrekordbox_adapter.rekordbox_db_candidates")
@patch("pyrekordbox.Rekordbox6Database")
def test_import_files_backs_up_before_opening_db(mock_cls, mock_candidates, mock_backup, tmp_path):
    """The master.db* snapshot always precedes Rekordbox6Database() on import."""
    db_path = tmp_path / "master.db"
    db_path.touch()
    mock_candidates.return_value = [db_path]

    calls = []
    mock_backup.side_effect = lambda path: calls.append("backup")
    db = _make_db()
    mock_cls.side_effect = lambda *a, **k: (calls.append("open"), db)[1]

    path = tmp_path / "song.aiff"
    path.write_bytes(b"x")
    RekordboxDbService().import_files([_item(path)])

    assert calls == ["backup", "open"]
    mock_backup.assert_called_once_with(db_path)


# ---------------------------------------------------------------------------
# _backup_db_files — backup copies are owner-only
# ---------------------------------------------------------------------------


def test_backup_db_files_outputs_are_owner_only(tmp_path, monkeypatch):
    """Backup copies of the whole library get 0o600 like credential files."""
    db_dir = tmp_path / "rekordbox"
    db_dir.mkdir()
    (db_dir / "master.db").write_bytes(b"main")
    (db_dir / "master.db-wal").write_bytes(b"wal")
    (db_dir / "master.db-shm").write_bytes(b"shm")
    data_dir = tmp_path / "userdata"
    monkeypatch.setattr("platformdirs.user_data_dir", lambda *a, **k: str(data_dir))

    rekordbox_db._backup_db_files(db_dir / "master.db")

    backups = sorted((data_dir / "backups" / "rekordbox").glob("master-*.db*"))
    assert len(backups) == 3
    for backup in backups:
        assert backup.stat().st_mode & 0o777 == 0o600
