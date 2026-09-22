"""Headless tests for RekordboxImportController (db-mode ingest + re-match)."""

from __future__ import annotations

import importlib
from unittest.mock import MagicMock

import pytest
from PySide6.QtTest import QSignalSpy

import rekordbox_set_list_manager.utils.config as config_mod
from rekordbox_set_list_manager.controllers.edit_controller import EditController
from rekordbox_set_list_manager.models.enums import MatchStatus
from rekordbox_set_list_manager.models.project import Project
from rekordbox_set_list_manager.models.track import Track
from rekordbox_set_list_manager.services.rekordbox_db import RekordboxDbError
from rekordbox_set_list_manager.services.skipped_item import SkippedItem
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


def _result(track_id: str, filepath) -> DownloadResult:
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


def project_track(pc, track):
    """Return the live project copy of *track* (post-mutation)."""
    return pc.project.get_track(track.id)


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


# ---------------------------------------------------------------------------
# Re-read retry — locked reads are retried, then degrade to insert IDs
# ---------------------------------------------------------------------------


def test_locked_reread_retries_with_injected_sleep(tmp_path):
    """A locked re-read waits _REREAD_DELAY_S and retries before giving up."""
    mod = _mod()
    file_path = tmp_path / "song.aiff"
    file_path.write_bytes(b"data")
    track = Track(title="Song", artist="Artist", tidal_id="t1")
    pc, ec = _make_ctrl(_project_with(track))
    fake_db = _FakeDb()
    fake_db.import_result = ({str(file_path): 42}, [])
    fake_db.collection_script = [
        RekordboxDbError("Failed to read Rekordbox database: database is locked"),
        [Track(title="Song", artist="Artist", filepath=str(file_path), rekordbox_id=42)],
    ]
    sleeps = []
    ctrl = mod.RekordboxImportController(pc, ec, db_service=fake_db, sleep=sleeps.append)

    result = ctrl.import_downloaded([_result("t1", file_path)])

    assert sleeps == [mod._REREAD_DELAY_S]
    assert fake_db.collection_calls == 2
    assert result.matched == 1
    assert project_track(pc, track).match_status == MatchStatus.MATCHED


def test_locked_reread_exhaustion_falls_back_to_inserted_ids(tmp_path):
    """Three locked attempts degrade to the authoritative insert-ID binding."""
    mod = _mod()
    file_path = tmp_path / "song.aiff"
    file_path.write_bytes(b"data")
    track = Track(title="Song", artist="Artist", tidal_id="t1")
    pc, ec = _make_ctrl(_project_with(track))
    fake_db = _FakeDb()
    fake_db.import_result = ({str(file_path): 77}, [])
    fake_db.collection_script = [
        RekordboxDbError("database is locked"),
        RekordboxDbError("database is locked"),
        RekordboxDbError("database is locked"),
    ]
    sleeps = []
    ctrl = mod.RekordboxImportController(pc, ec, db_service=fake_db, sleep=sleeps.append)

    result = ctrl.import_downloaded([_result("t1", file_path)])

    assert fake_db.collection_calls == mod._REREAD_ATTEMPTS
    assert sleeps == [mod._REREAD_DELAY_S] * (mod._REREAD_ATTEMPTS - 1)
    assert result.matched == 1
    updated = project_track(pc, track)
    assert updated.match_status == MatchStatus.MATCHED
    assert updated.rekordbox_id == 77
    assert updated.filepath == str(file_path)


def test_nonlocked_reread_error_degrades_to_insert_ids(tmp_path):
    """A non-lock RekordboxDbError degrades immediately — no retries."""
    mod = _mod()
    file_path = tmp_path / "song.aiff"
    file_path.write_bytes(b"data")
    track = Track(title="Song", artist="Artist", tidal_id="t1")
    pc, ec = _make_ctrl(_project_with(track))
    fake_db = _FakeDb()
    fake_db.import_result = ({str(file_path): 88}, [])
    fake_db.collection_script = [RekordboxDbError("disk vanished mid-read")]
    sleeps = []
    ctrl = mod.RekordboxImportController(pc, ec, db_service=fake_db, sleep=sleeps.append)

    result = ctrl.import_downloaded([_result("t1", file_path)])

    assert fake_db.collection_calls == 1  # non-lock errors never retry
    assert sleeps == []
    assert result.matched == 1
    assert project_track(pc, track).rekordbox_id == 88


# ---------------------------------------------------------------------------
# Deterministic re-match — ISRC fallback, lowest-id, protection, isolation
# ---------------------------------------------------------------------------


def test_isrc_fallback_binds_collection_row_on_different_path(tmp_path):
    """No filepath hit → the shared ISRC joins the project track."""
    mod = _mod()
    file_path = tmp_path / "download.aiff"
    file_path.write_bytes(b"data")
    track = Track(title="Song", artist="Artist", tidal_id="t1", isrc="USAA1")
    pc, ec = _make_ctrl(_project_with(track))
    fake_db = _FakeDb()
    fake_db.import_result = ({str(file_path): 9}, [])
    fake_db.collection_script = [
        [
            Track(
                title="Song",
                artist="Artist",
                filepath="/elsewhere/renamed.aiff",
                isrc="USAA1",
                rekordbox_id=55,
            )
        ]
    ]
    ctrl = mod.RekordboxImportController(pc, ec, db_service=fake_db)

    result = ctrl.import_downloaded([_result("t1", file_path)])

    updated = project_track(pc, track)
    assert result.matched == 1
    assert updated.match_status == MatchStatus.MATCHED
    assert updated.match_strategy == "isrc"
    assert updated.rekordbox_id == 55


def test_duplicate_isrc_resolves_lowest_rekordbox_id(tmp_path):
    """Ambiguous ISRC picks the lowest rekordbox_id, not list order."""
    mod = _mod()
    file_path = tmp_path / "download.aiff"
    file_path.write_bytes(b"data")
    track = Track(title="Song", artist="Artist", tidal_id="t1", isrc="USAA1")
    pc, ec = _make_ctrl(_project_with(track))
    fake_db = _FakeDb()
    fake_db.collection_script = [
        [
            # Deliberately reversed order — the answer must come from the ID.
            Track(title="S", artist="A", filepath="/dup/hi.aiff", isrc="USAA1", rekordbox_id=99),
            Track(title="S", artist="A", filepath="/dup/lo.aiff", isrc="USAA1", rekordbox_id=12),
        ]
    ]
    ctrl = mod.RekordboxImportController(pc, ec, db_service=fake_db)

    ctrl.import_downloaded([_result("t1", file_path)])

    assert project_track(pc, track).rekordbox_id == 12


def test_manually_matched_track_is_never_rebound(tmp_path):
    """A user's manual binding wins — reported in unmatched, left untouched."""
    mod = _mod()
    file_path = tmp_path / "song.aiff"
    file_path.write_bytes(b"data")
    track = Track(
        title="Song",
        artist="Artist",
        tidal_id="t1",
        filepath="/hand/picked.aiff",
        match_status=MatchStatus.MANUALLY_MATCHED,
    )
    pc, ec = _make_ctrl(_project_with(track))
    fake_db = _FakeDb()
    fake_db.collection_script = [
        [Track(title="Song", artist="Artist", filepath=str(file_path), rekordbox_id=1)]
    ]
    ctrl = mod.RekordboxImportController(pc, ec, db_service=fake_db)
    undo_before = len(ec._stack._undo)

    result = ctrl.import_downloaded([_result("t1", file_path)])

    kept = project_track(pc, track)
    assert kept.match_status == MatchStatus.MANUALLY_MATCHED
    assert kept.filepath == "/hand/picked.aiff"
    assert result.matched == 0
    assert any(
        s.label == track.display_name and "kept existing binding" in s.reason
        for s in result.unmatched
    )
    assert len(ec._stack._undo) == undo_before


def test_neighbor_track_sharing_isrc_is_never_touched(tmp_path):
    """Only tracks in the download set resolve — a same-ISRC neighbor stays put."""
    mod = _mod()
    file_path = tmp_path / "song.aiff"
    file_path.write_bytes(b"data")
    wanted = Track(title="Song", artist="Artist", tidal_id="t1", isrc="USAA1")
    neighbor = Track(title="Same ISRC", artist="Artist", tidal_id="t2", isrc="USAA1")
    pc, ec = _make_ctrl(_project_with(wanted, neighbor))
    fake_db = _FakeDb()
    fake_db.collection_script = [
        [Track(title="S", artist="A", filepath=str(file_path), isrc="USAA1", rekordbox_id=7)]
    ]
    ctrl = mod.RekordboxImportController(pc, ec, db_service=fake_db)

    ctrl.import_downloaded([_result("t1", file_path)])

    assert project_track(pc, wanted).match_status == MatchStatus.MATCHED
    assert project_track(pc, neighbor).match_status == MatchStatus.UNMATCHED
    assert project_track(pc, neighbor).rekordbox_id is None


def test_result_without_project_track_is_unmatched_but_still_ingested(tmp_path):
    """No tidal_id join → unmatched reason, yet the file still enters Rekordbox."""
    mod = _mod()
    file_path = tmp_path / "orphan-download.aiff"
    file_path.write_bytes(b"data")
    pc, ec = _make_ctrl(_project_with(Track(title="S", artist="A", tidal_id="t1")))
    fake_db = _FakeDb()
    fake_db.collection_script = [
        [Track(title="orphan-download", artist="", filepath=str(file_path), rekordbox_id=3)]
    ]
    ctrl = mod.RekordboxImportController(pc, ec, db_service=fake_db)

    result = ctrl.import_downloaded([_result("t999", file_path)])

    assert len(fake_db.import_calls) == 1  # ingest still ran
    assert fake_db.import_calls[0][0].title == "orphan-download"  # stem-derived Title
    assert result.matched == 0
    assert any(
        s.label == file_path.name and "no project track with this Tidal ID" in s.reason
        for s in result.unmatched
    )


# ---------------------------------------------------------------------------
# Idempotent re-run and telemetry hygiene
# ---------------------------------------------------------------------------


def test_rerun_is_idempotent_and_grows_no_undo(tmp_path):
    """Re-importing the same download skips dedup and leaves undo depth flat."""
    mod = _mod()
    file_path = tmp_path / "song.aiff"
    file_path.write_bytes(b"data")
    track = Track(title="Song", artist="Artist", tidal_id="t1")
    pc, ec = _make_ctrl(_project_with(track))
    fake_db = _FakeDb()
    fake_db.import_result = ({str(file_path): 42}, [])
    fake_db.collection_script = [
        [Track(title="Song", artist="Artist", filepath=str(file_path), rekordbox_id=42)]
    ]
    ctrl = mod.RekordboxImportController(pc, ec, db_service=fake_db)

    first = ctrl.import_downloaded([_result("t1", file_path)])
    undo_after_first = len(ec._stack._undo)
    assert first.inserted == 1

    # Second pass: the row already exists → dedup skip, no insert, and the
    # re-read now sees the collection row.
    fake_db.import_result = ({}, [SkippedItem(file_path.name, "not imported: already exists")])
    fake_db.collection_script = [
        [Track(title="Song", artist="Artist", filepath=str(file_path), rekordbox_id=42)]
    ]
    second = ctrl.import_downloaded([_result("t1", file_path)])

    assert second.inserted == 0
    assert len(second.skipped) == 1
    assert second.matched == 1
    assert project_track(pc, track).match_status == MatchStatus.MATCHED
    assert len(ec._stack._undo) == undo_after_first


def test_telemetry_uses_only_allowlisted_fields(tmp_path, monkeypatch):
    """The rekordbox_import event carries no paths, titles, or extra keys."""
    mod = _mod()
    file_path = tmp_path / "song.aiff"
    file_path.write_bytes(b"data")
    track = Track(title="Secret Track", artist="Artist", tidal_id="t1", isrc="USAA1")
    pc, ec = _make_ctrl(_project_with(track))
    fake_db = _FakeDb()
    fake_db.collection_script = [
        [Track(title="Song", artist="Artist", filepath=str(file_path), rekordbox_id=5)]
    ]
    captured = []
    monkeypatch.setattr(
        "rekordbox_set_list_manager.services.telemetry.record",
        lambda event, **kw: captured.append((event, kw)),
    )
    ctrl = mod.RekordboxImportController(pc, ec, db_service=fake_db)

    ctrl.import_downloaded([_result("t1", file_path)])

    assert len(captured) == 1
    event, kwargs = captured[0]
    assert event == "rekordbox_import"
    allowed = {"version", "track_count", "total", "matched"}
    for key in kwargs:
        assert key in allowed or key.startswith("strategy_"), key
    # No user content leaks — neither paths nor titles appear as values.
    for value in kwargs.values():
        assert not isinstance(value, str) or (str(file_path) not in value and "Secret" not in value)


def test_controller_module_never_imports_the_fuzzy_matcher():
    """The deterministic join must never construct or consult TrackMatcher."""
    import inspect

    source = inspect.getsource(_mod())
    assert "TrackMatcher" not in source
    assert "thefuzz" not in source


def test_only_deterministic_strategies_are_stamped(tmp_path):
    """A mixed batch stamps only 'filepath'/'isrc' — never fuzzy variants."""
    mod = _mod()
    p1 = tmp_path / "one.aiff"
    p1.write_bytes(b"1")
    p2 = tmp_path / "two.aiff"
    p2.write_bytes(b"2")
    by_path = Track(title="A", artist="X", tidal_id="t1")
    by_isrc = Track(title="B", artist="Y", tidal_id="t2", isrc="USBB2")
    pc, ec = _make_ctrl(_project_with(by_path, by_isrc))
    fake_db = _FakeDb()
    fake_db.collection_script = [
        [
            Track(title="A", artist="X", filepath=str(p1), rekordbox_id=1),
            Track(title="B", artist="Y", filepath="/other/b.aiff", isrc="USBB2", rekordbox_id=2),
        ]
    ]
    ctrl = mod.RekordboxImportController(pc, ec, db_service=fake_db)

    ctrl.import_downloaded([_result("t1", p1), _result("t2", p2)])

    strategies = {
        project_track(pc, by_path).match_strategy,
        project_track(pc, by_isrc).match_strategy,
    }
    assert strategies == {"filepath", "isrc"}
