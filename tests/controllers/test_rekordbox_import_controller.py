"""Headless tests for RekordboxImportController (db-mode ingest + re-match)."""

from __future__ import annotations

import importlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PySide6.QtTest import QSignalSpy

import rekordbox_set_list_manager.utils.config as config_mod
from rekordbox_set_list_manager.controllers.edit_controller import EditController
from rekordbox_set_list_manager.models.enums import MatchStatus
from rekordbox_set_list_manager.models.project import Project
from rekordbox_set_list_manager.models.track import Track
from rekordbox_set_list_manager.services.tidal_download import DownloadResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mod():
    """Import the controller module under test."""
    return importlib.import_module(
        "rekordbox_set_list_manager.controllers.rekordbox_import_controller"
    )


@pytest.fixture(autouse=True)
def _app(qapp):
    """Real QObjects and signal capture need a QApplication instance."""


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path, monkeypatch):
    """Redirect config to a per-test dir so rekordbox_import_mode is hermetic."""
    config_dir = tmp_path / "config"
    monkeypatch.setattr(config_mod, "_CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_mod, "_CONFIG_PATH", config_dir / "config.json")
    monkeypatch.setattr(config_mod, "_cache", None)


class _FakeDb:
    """Scriptable stand-in for RekordboxDbService (import_files + get_collection)."""

    def __init__(self) -> None:
        self.import_calls: list[list] = []
        self.import_result: object = ({}, [])
        self.collection_calls = 0
        self.collection_script: list = []

    def import_files(self, items):
        items = list(items)
        self.import_calls.append(items)
        if isinstance(self.import_result, BaseException):
            raise self.import_result
        return self.import_result

    def get_collection(self):
        self.collection_calls += 1
        outcome = self.collection_script.pop(0) if self.collection_script else []
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _result(track_id: str, filepath: Path) -> DownloadResult:
    return DownloadResult(
        filepath=filepath,
        track_id=track_id,
        served_quality="LOSSLESS",
        requested_quality="HI_RES_LOSSLESS",
        downgraded=False,
        codec="flac",
        duration_s=180.0,
    )


def _make_ctrl(project):
    """Real EditController over a mock ProjectController holding *project*."""
    pc = MagicMock()
    pc.project = project

    def _restore(p):
        pc.project = p

    pc.restore.side_effect = _restore
    ec = EditController(pc)
    return pc, ec


def _project_with(*tracks: Track) -> Project:
    project = Project(name="Set")
    for track in tracks:
        project.add_track(track)
    return project


# ---------------------------------------------------------------------------
# import_downloaded — db-mode happy path
# ---------------------------------------------------------------------------


def test_import_downloaded_matches_and_undoes_in_one_step(tmp_path):
    mod = _mod()
    file_path = tmp_path / "song.aiff"
    file_path.write_bytes(b"data")
    track = Track(
        title="Song",
        artist="Artist",
        tidal_id="t1",
        isrc="USAA1",
        match_status=MatchStatus.UNMATCHED,
    )
    project = _project_with(track)
    pc, ec = _make_ctrl(project)
    fake_db = _FakeDb()
    fake_db.import_result = ({str(file_path): 42}, [])
    fake_db.collection_script = [
        [Track(title="Song", artist="Artist", filepath=str(file_path), rekordbox_id=42)]
    ]
    ctrl = mod.RekordboxImportController(pc, ec, db_service=fake_db)
    spy = QSignalSpy(ctrl.import_finished)
    undo_before = len(ec._stack._undo)

    result = ctrl.import_downloaded([_result("t1", file_path)])

    # The download joined by filepath and bound the project track.
    updated = project.get_track(track.id)
    assert updated.match_status == MatchStatus.MATCHED
    assert updated.filepath == str(file_path)
    assert updated.rekordbox_id == 42
    assert updated.match_strategy == "filepath"
    assert updated.match_score == 1.0

    # Exactly one undo entry regardless of track count.
    assert len(ec._stack._undo) == undo_before + 1

    # Result object + signal carry the ingest outcome.
    assert result.mode == "db"
    assert result.inserted == 1
    assert result.matched == 1
    assert result.skipped == []
    assert result.unmatched == []
    assert result.needs_reload_tag is True
    assert result.pending_import is False
    assert result.xml_path is None
    assert spy.count() == 1
    assert spy.at(0)[0] is result

    # Undo restores UNMATCHED; the file on disk is not model state and survives.
    ec.undo()
    restored = pc.project.get_track(track.id)
    assert restored.match_status == MatchStatus.UNMATCHED
    assert file_path.exists()


def test_import_downloaded_ingest_items_carry_track_metadata(tmp_path):
    """import_files receives one ImportItem per result with title/isrc stamped."""
    mod = _mod()
    file_path = tmp_path / "song.aiff"
    file_path.write_bytes(b"data")
    track = Track(title="Song", artist="Artist", tidal_id="t1", isrc="USAA1")
    pc, ec = _make_ctrl(_project_with(track))
    fake_db = _FakeDb()
    ctrl = mod.RekordboxImportController(pc, ec, db_service=fake_db)

    ctrl.import_downloaded([_result("t1", file_path)])

    assert len(fake_db.import_calls) == 1
    items = fake_db.import_calls[0]
    assert len(items) == 1
    assert items[0].path == file_path
    assert items[0].title == "Song"
    assert items[0].isrc == "USAA1"


# ---------------------------------------------------------------------------
# import_downloaded — classified failures and no-ops
# ---------------------------------------------------------------------------


def test_import_downloaded_unknown_mode_raises(tmp_path):
    mod = _mod()
    config_mod.set_value("rekordbox_import_mode", "ftp")
    track = Track(title="Song", artist="Artist", tidal_id="t1")
    pc, ec = _make_ctrl(_project_with(track))
    fake_db = _FakeDb()
    ctrl = mod.RekordboxImportController(pc, ec, db_service=fake_db)

    with pytest.raises(mod.RekordboxImportError, match="Unknown rekordbox_import_mode"):
        ctrl.import_downloaded([_result("t1", tmp_path / "x.aiff")])

    assert fake_db.import_calls == []


def test_import_downloaded_xml_mode_not_supported_yet(tmp_path):
    mod = _mod()
    config_mod.set_value("rekordbox_import_mode", "xml")
    track = Track(title="Song", artist="Artist", tidal_id="t1")
    pc, ec = _make_ctrl(_project_with(track))
    fake_db = _FakeDb()
    ctrl = mod.RekordboxImportController(pc, ec, db_service=fake_db)

    with pytest.raises(mod.RekordboxImportError, match="not supported yet"):
        ctrl.import_downloaded([_result("t1", tmp_path / "x.aiff")])

    assert fake_db.import_calls == []


def test_import_downloaded_raises_without_project(tmp_path):
    mod = _mod()
    pc, ec = _make_ctrl(None)
    fake_db = _FakeDb()
    ctrl = mod.RekordboxImportController(pc, ec, db_service=fake_db)

    with pytest.raises(mod.RekordboxImportError, match="No project is loaded"):
        ctrl.import_downloaded([_result("t1", tmp_path / "x.aiff")])

    assert fake_db.import_calls == []


def test_import_downloaded_empty_results_is_noop():
    mod = _mod()
    pc, ec = _make_ctrl(_project_with(Track(title="S", artist="A", tidal_id="t1")))
    fake_db = _FakeDb()
    ctrl = mod.RekordboxImportController(pc, ec, db_service=fake_db)
    spy = QSignalSpy(ctrl.import_finished)
    undo_before = len(ec._stack._undo)

    result = ctrl.import_downloaded([])

    assert result.mode == "db"
    assert result.inserted == 0
    assert result.matched == 0
    assert result.skipped == []
    assert result.unmatched == []
    assert result.needs_reload_tag is False
    assert fake_db.import_calls == []
    assert fake_db.collection_calls == 0
    assert len(ec._stack._undo) == undo_before
    assert spy.count() == 1
    assert spy.at(0)[0] is result
