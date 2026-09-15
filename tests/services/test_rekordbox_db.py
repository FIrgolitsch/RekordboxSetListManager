"""Tests for RekordboxDbService using mocked pyrekordbox."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from rekordbox_set_list_manager.models.enums import MatchStatus, RekordboxColor, TrackSource
from rekordbox_set_list_manager.models.section import Section
from rekordbox_set_list_manager.models.track import Track
from rekordbox_set_list_manager.services.rekordbox_db import (
    RekordboxDbError,
    RekordboxDbService,
    _parse_color,
)
from rekordbox_set_list_manager.services.skipped_item import SkippedItem

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_content(
    *,
    track_id: str = "1001",
    title: str | None = "Test Track",
    artist_name: str = "Test Artist",
    bpm: int | None = 12800,
    length: int = 240,
    key_name: str | None = "8A",
    folder_path: str | None = "/music/test.mp3",
    isrc: str | None = "USABC1234567",
    color_name: str | None = "Blue",
    service_id: int = 0,
    src_id: str | None = None,
) -> MagicMock:
    content = MagicMock()
    content.ID = track_id
    content.Title = title
    content.ArtistName = artist_name
    content.BPM = bpm
    content.Length = length
    content.KeyName = key_name
    content.FolderPath = folder_path
    content.ISRC = isrc
    content.ColorName = color_name
    content.ServiceID = service_id
    content.SrcID = src_id
    return content


def _make_db(contents: list[MagicMock]) -> MagicMock:
    """Return a mock Rekordbox6Database whose get_content().all() returns *contents*."""
    db = MagicMock()
    query = MagicMock()
    query.all.return_value = contents
    db.get_content.return_value = query
    # Support context manager protocol
    db.__enter__ = lambda self: self
    db.__exit__ = MagicMock(return_value=False)
    return db


def _make_export_db() -> MagicMock:
    """Return a mock Rekordbox6Database ready for export_set_to_db.

    Configured with no existing playlists so the idempotent-delete loop is a
    no-op; ``create_playlist`` returns a mock playlist object.
    """
    db = MagicMock()
    db.__enter__ = lambda self: self
    db.__exit__ = MagicMock(return_value=False)
    db.get_playlist.return_value.all.return_value = []
    db.create_playlist.return_value = MagicMock()
    return db


# ---------------------------------------------------------------------------
# is_available
# ---------------------------------------------------------------------------


@patch("pyrekordbox.Rekordbox6Database")
def test_is_available_returns_true_when_db_opens(mock_cls):
    db = MagicMock()
    db.__enter__ = lambda self: self
    db.__exit__ = MagicMock(return_value=False)
    mock_cls.return_value = db

    assert RekordboxDbService().is_available() is True


@patch("pyrekordbox.Rekordbox6Database")
def test_is_available_returns_false_on_exception(mock_cls):
    mock_cls.side_effect = RuntimeError("no DB found")

    assert RekordboxDbService().is_available() is False


# ---------------------------------------------------------------------------
# get_db_path
# ---------------------------------------------------------------------------


@patch("rekordbox_set_list_manager.services._pyrekordbox_adapter.rekordbox_db_candidates")
def test_get_db_path_returns_first_candidate(mock_candidates, tmp_path):
    db_path = tmp_path / "master.db"
    db_path.touch()
    mock_candidates.return_value = [db_path, tmp_path / "other.db"]

    assert RekordboxDbService().get_db_path() == db_path


@patch("rekordbox_set_list_manager.services._pyrekordbox_adapter.rekordbox_db_candidates")
def test_get_db_path_returns_none_when_no_candidates(mock_candidates):
    mock_candidates.return_value = []

    assert RekordboxDbService().get_db_path() is None


@patch("rekordbox_set_list_manager.services._pyrekordbox_adapter.rekordbox_db_candidates")
def test_get_db_path_returns_none_on_adapter_error(mock_candidates):
    mock_candidates.side_effect = RuntimeError("pyrekordbox broke")

    assert RekordboxDbService().get_db_path() is None


# ---------------------------------------------------------------------------
# get_collection
# ---------------------------------------------------------------------------


@patch("pyrekordbox.Rekordbox6Database")
def test_get_collection_returns_tracks(mock_cls):
    content = _make_content()
    mock_cls.return_value = _make_db([content])

    tracks = RekordboxDbService().get_collection()

    assert len(tracks) == 1
    t = tracks[0]
    assert t.title == "Test Track"
    assert t.artist == "Test Artist"
    assert t.bpm == pytest.approx(128.0)
    assert t.duration == 240
    assert t.key == "8A"
    assert t.filepath == "/music/test.mp3"
    assert t.isrc == "USABC1234567"
    assert t.rekordbox_id == 1001
    assert t.source == TrackSource.REKORDBOX
    assert t.match_status == MatchStatus.UNMATCHED
    assert t.color == RekordboxColor.BLUE


@patch("pyrekordbox.Rekordbox6Database")
def test_get_collection_skips_tracks_without_title(mock_cls):
    no_title = _make_content(title=None)
    valid = _make_content(track_id="2", title="Valid")
    mock_cls.return_value = _make_db([no_title, valid])

    tracks = RekordboxDbService().get_collection()

    assert len(tracks) == 1
    assert tracks[0].title == "Valid"


@patch("pyrekordbox.Rekordbox6Database")
def test_get_collection_skips_streaming_tracks(mock_cls):
    local = _make_content(track_id="1", title="Local Track", service_id=0)
    streaming = _make_content(track_id="2", title="Streaming Track", service_id=1)
    mock_cls.return_value = _make_db([local, streaming])

    tracks = RekordboxDbService().get_collection()

    assert len(tracks) == 1
    assert tracks[0].title == "Local Track"


@patch("pyrekordbox.Rekordbox6Database")
def test_get_collection_skips_streaming_tracks_by_src_id(mock_cls):
    local = _make_content(track_id="1", title="Local Track", service_id=0, src_id=None)
    streaming = _make_content(
        track_id="2", title="Streaming Track", service_id=0, src_id="12345678"
    )
    mock_cls.return_value = _make_db([local, streaming])

    tracks = RekordboxDbService().get_collection()

    assert len(tracks) == 1
    assert tracks[0].title == "Local Track"


@patch("pyrekordbox.Rekordbox6Database")
def test_get_collection_skips_tracks_without_filepath(mock_cls):
    no_path = _make_content(track_id="1", title="No Path Track", folder_path=None)
    with_path = _make_content(track_id="2", title="With Path Track")
    mock_cls.return_value = _make_db([no_path, with_path])

    tracks = RekordboxDbService().get_collection()

    assert len(tracks) == 1
    assert tracks[0].title == "With Path Track"


@patch("pyrekordbox.Rekordbox6Database")
def test_get_collection_skips_tracks_with_streaming_url_path(mock_cls):
    tidal = _make_content(track_id="1", title="Tidal Track", folder_path="tidal://tracks/12345678")
    local = _make_content(track_id="2", title="Local Track")
    mock_cls.return_value = _make_db([tidal, local])

    tracks = RekordboxDbService().get_collection()

    assert len(tracks) == 1
    assert tracks[0].title == "Local Track"


@patch("pyrekordbox.Rekordbox6Database")
def test_get_collection_skips_tracks_with_non_absolute_path(mock_cls):
    """Apple Music and similar services store paths like 'apple-music:tracks:123'."""
    apple = _make_content(
        track_id="1", title="Apple Music Track", folder_path="apple-music:tracks:1751268186"
    )
    local = _make_content(track_id="2", title="Local Track")
    mock_cls.return_value = _make_db([apple, local])

    tracks = RekordboxDbService().get_collection()

    assert len(tracks) == 1
    assert tracks[0].title == "Local Track"


@patch("pyrekordbox.Rekordbox6Database")
def test_get_collection_handles_null_bpm(mock_cls):
    content = _make_content(bpm=None)
    mock_cls.return_value = _make_db([content])

    tracks = RekordboxDbService().get_collection()

    assert tracks[0].bpm is None


@patch("pyrekordbox.Rekordbox6Database")
def test_get_collection_handles_null_optional_fields(mock_cls):
    content = _make_content(isrc=None, color_name=None, key_name=None)
    mock_cls.return_value = _make_db([content])

    tracks = RekordboxDbService().get_collection()

    t = tracks[0]
    assert t.isrc is None
    assert t.color is None
    assert t.key is None


@patch("pyrekordbox.Rekordbox6Database")
def test_get_collection_raises_on_db_error(mock_cls):
    mock_cls.side_effect = RuntimeError("DB locked")

    with pytest.raises(RekordboxDbError, match="Failed to read Rekordbox database"):
        RekordboxDbService().get_collection()


# ---------------------------------------------------------------------------
# get_collection — filepath handling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "folder_path",
    [
        "C:\\music\\x.mp3",  # Windows drive-absolute
        "D:/tracks/y.wav",  # Windows drive-absolute, forward slashes
        "\\\\server\\share\\x.mp3",  # UNC share
    ],
)
@patch("pyrekordbox.Rekordbox6Database")
def test_get_collection_accepts_windows_absolute_paths(mock_cls, folder_path):
    """Rekordbox on Windows stores FolderPath values like C:\\music\\x.mp3."""
    content = _make_content(folder_path=folder_path)
    mock_cls.return_value = _make_db([content])

    tracks = RekordboxDbService().get_collection()

    assert len(tracks) == 1
    assert tracks[0].filepath == folder_path


@pytest.mark.parametrize(
    "folder_path",
    [
        "C:rel.mp3",  # drive-relative — deliberately not absolute
        "rel/x.mp3",  # plain relative path
        "",  # empty string
    ],
)
@patch("pyrekordbox.Rekordbox6Database")
def test_get_collection_rejects_non_absolute_paths(mock_cls, folder_path):
    content = _make_content(folder_path=folder_path)
    mock_cls.return_value = _make_db([content])

    assert RekordboxDbService().get_collection() == []


# ---------------------------------------------------------------------------
# find_track_by_isrc
# ---------------------------------------------------------------------------


@patch("pyrekordbox.Rekordbox6Database")
def test_find_track_by_isrc_returns_track_when_found(mock_cls):
    content = _make_content(isrc="DEBL41500001")
    db = MagicMock()
    db.__enter__ = lambda self: self
    db.__exit__ = MagicMock(return_value=False)
    query = MagicMock()
    query.all.return_value = [content]
    db.get_content.return_value = query
    mock_cls.return_value = db

    track = RekordboxDbService().find_track_by_isrc("DEBL41500001")

    assert track is not None
    assert track.isrc == "DEBL41500001"
    db.get_content.assert_called_once_with(ISRC="DEBL41500001")


@patch("pyrekordbox.Rekordbox6Database")
def test_find_track_by_isrc_returns_none_when_not_found(mock_cls):
    db = MagicMock()
    db.__enter__ = lambda self: self
    db.__exit__ = MagicMock(return_value=False)
    query = MagicMock()
    query.all.return_value = []
    db.get_content.return_value = query
    mock_cls.return_value = db

    assert RekordboxDbService().find_track_by_isrc("NOTFOUND") is None


@patch("pyrekordbox.Rekordbox6Database")
def test_find_track_by_isrc_resolves_duplicates_to_lowest_id(mock_cls):
    """Duplicate ISRCs resolve deterministically to the lowest content ID."""
    high = _make_content(track_id="200", title="Dup High")
    low = _make_content(track_id="100", title="Dup Low")
    db = MagicMock()
    db.__enter__ = lambda self: self
    db.__exit__ = MagicMock(return_value=False)
    query = MagicMock()
    query.all.return_value = [high, low]  # DB order is not the pick order
    db.get_content.return_value = query
    mock_cls.return_value = db

    track = RekordboxDbService().find_track_by_isrc("DEBL41500001")

    assert track is not None
    assert track.rekordbox_id == 100


@patch("pyrekordbox.Rekordbox6Database")
def test_find_track_by_isrc_logs_debug_on_duplicate_isrc(mock_cls, caplog):
    """Ambiguous ISRC rows are logged at debug, not silently collapsed."""
    db = MagicMock()
    db.__enter__ = lambda self: self
    db.__exit__ = MagicMock(return_value=False)
    query = MagicMock()
    query.all.return_value = [
        _make_content(track_id="200"),
        _make_content(track_id="100"),
    ]
    db.get_content.return_value = query
    mock_cls.return_value = db

    with caplog.at_level(logging.DEBUG, logger="rekordbox_set_list_manager.services.rekordbox_db"):
        RekordboxDbService().find_track_by_isrc("DEBL41500001")

    assert any(r.levelno == logging.DEBUG for r in caplog.records)


# ---------------------------------------------------------------------------
# export_set_to_db
# ---------------------------------------------------------------------------


@patch("pyrekordbox.Rekordbox6Database")
def test_export_set_to_db_writes_resolvable_tracks(mock_cls):
    db = _make_export_db()
    db.get_content.return_value = MagicMock()  # any lookup resolves to a row
    mock_cls.return_value = db

    track = Track(title="Song", artist="Artist", rekordbox_id=42, filepath="/music/song.mp3")
    section = Section(name="Peak", track_ids=[track.id])

    exported, skipped = RekordboxDbService().export_set_to_db(
        [section], "My Set", {track.id: track}
    )

    assert exported == 1
    assert skipped == []
    db.add_to_playlist.assert_called_once()
    db.commit.assert_called_once_with()


@patch("pyrekordbox.Rekordbox6Database")
def test_export_set_to_db_reports_unknown_track_ids(mock_cls):
    db = _make_export_db()
    mock_cls.return_value = db

    missing_id = uuid4()
    section = Section(name="Peak", track_ids=[missing_id])

    exported, skipped = RekordboxDbService().export_set_to_db([section], "My Set", {})

    assert exported == 0
    assert skipped == [SkippedItem(str(missing_id), "not in project tracks")]


@patch("pyrekordbox.Rekordbox6Database")
def test_export_set_to_db_reports_unresolvable_tracks(mock_cls):
    db = _make_export_db()
    db.get_content.return_value = None  # neither ID nor filepath lookup resolves
    mock_cls.return_value = db

    track = Track(title="Lost Song", artist="Lost Artist", filepath="/music/gone.mp3")
    section = Section(name="Peak", track_ids=[track.id])

    exported, skipped = RekordboxDbService().export_set_to_db(
        [section], "My Set", {track.id: track}
    )

    assert exported == 0
    assert skipped == [SkippedItem(track.display_name, "not found in Rekordbox collection")]


@patch("pyrekordbox.Rekordbox6Database")
def test_export_set_to_db_reports_track_without_any_reference(mock_cls):
    """A track with no rekordbox_id and no filepath can never resolve."""
    db = _make_export_db()
    mock_cls.return_value = db

    track = Track(title="Stream Only", artist="Someone")
    section = Section(name="Peak", track_ids=[track.id])

    exported, skipped = RekordboxDbService().export_set_to_db(
        [section], "My Set", {track.id: track}
    )

    assert exported == 0
    assert len(skipped) == 1
    assert skipped[0].reason == "not found in Rekordbox collection"
    db.get_content.assert_not_called()


# ---------------------------------------------------------------------------
# find_track_by_path
# ---------------------------------------------------------------------------


@patch("pyrekordbox.Rekordbox6Database")
def test_find_track_by_path_returns_track_when_found(mock_cls):
    path = "/music/track.mp3"
    content = _make_content(folder_path=path)
    db = MagicMock()
    db.__enter__ = lambda self: self
    db.__exit__ = MagicMock(return_value=False)
    query = MagicMock()
    query.first.return_value = content
    db.get_content.return_value = query
    mock_cls.return_value = db

    track = RekordboxDbService().find_track_by_path(path)

    assert track is not None
    assert track.filepath == path
    db.get_content.assert_called_once_with(FolderPath=path)


@patch("pyrekordbox.Rekordbox6Database")
def test_find_track_by_path_returns_none_when_not_found(mock_cls):
    db = MagicMock()
    db.__enter__ = lambda self: self
    db.__exit__ = MagicMock(return_value=False)
    query = MagicMock()
    query.first.return_value = None
    db.get_content.return_value = query
    mock_cls.return_value = db

    assert RekordboxDbService().find_track_by_path("/not/exist.mp3") is None


# ---------------------------------------------------------------------------
# _parse_color
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Pink", RekordboxColor.PINK),
        ("RED", RekordboxColor.RED),
        ("orange", RekordboxColor.ORANGE),
        ("Yellow", RekordboxColor.YELLOW),
        ("green", RekordboxColor.GREEN),
        ("Aqua", RekordboxColor.AQUA),
        ("cyan", RekordboxColor.AQUA),
        ("Blue", RekordboxColor.BLUE),
        ("PURPLE", RekordboxColor.PURPLE),
        ("unknown", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_color(name, expected):
    assert _parse_color(name) == expected


# ---------------------------------------------------------------------------
# export_set_to_db — running guard and pre-export backup
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _rekordbox_not_running(monkeypatch):
    """Stub the process check so tests never depend on local machine state."""
    monkeypatch.setattr(
        "rekordbox_set_list_manager.services.rekordbox_db.get_rekordbox_pid",
        lambda *a, **k: 0,
        raising=False,
    )


@patch("rekordbox_set_list_manager.services.rekordbox_db.get_rekordbox_pid")
@patch("rekordbox_set_list_manager.services.rekordbox_db._backup_db_files")
@patch("pyrekordbox.Rekordbox6Database")
def test_export_set_to_db_raises_when_rekordbox_running(mock_cls, mock_backup, mock_pid):
    mock_pid.return_value = 1234

    with pytest.raises(RekordboxDbError, match="Rekordbox is running — close it before exporting"):
        RekordboxDbService().export_set_to_db([], "My Set", {})

    mock_backup.assert_not_called()
    mock_cls.assert_not_called()


@patch("rekordbox_set_list_manager.services.rekordbox_db.get_rekordbox_pid")
@patch("rekordbox_set_list_manager.services.rekordbox_db._backup_db_files")
@patch("rekordbox_set_list_manager.services._pyrekordbox_adapter.rekordbox_db_candidates")
@patch("pyrekordbox.Rekordbox6Database")
def test_export_set_to_db_backs_up_before_opening_db(
    mock_cls, mock_candidates, mock_backup, mock_pid, tmp_path
):
    """The backup must complete before Rekordbox6Database opens master.db."""
    mock_pid.return_value = 0
    db_path = tmp_path / "master.db"
    db_path.touch()
    mock_candidates.return_value = [db_path]

    calls = []
    mock_backup.side_effect = lambda path: calls.append("backup")
    db = _make_export_db()
    mock_cls.side_effect = lambda *a, **k: (calls.append("open"), db)[1]

    RekordboxDbService().export_set_to_db([], "My Set", {})

    assert calls == ["backup", "open"]
    mock_backup.assert_called_once_with(db_path)


@patch("rekordbox_set_list_manager.services.rekordbox_db.get_rekordbox_pid")
@patch("rekordbox_set_list_manager.services.rekordbox_db._backup_db_files")
@patch("rekordbox_set_list_manager.services._pyrekordbox_adapter.rekordbox_db_candidates")
@patch("pyrekordbox.Rekordbox6Database")
def test_export_set_to_db_skips_backup_when_db_path_unknown(
    mock_cls, mock_candidates, mock_backup, mock_pid
):
    """No detected DB path → no backup; export still proceeds to its open."""
    mock_pid.return_value = 0
    mock_candidates.return_value = []
    mock_cls.return_value = _make_export_db()

    exported, skipped = RekordboxDbService().export_set_to_db([], "My Set", {})

    assert exported == 0
    assert skipped == []
    mock_backup.assert_not_called()
    mock_cls.assert_called_once()


# ---------------------------------------------------------------------------
# _backup_db_files
# ---------------------------------------------------------------------------


def _fake_user_data_dir(monkeypatch, data_dir):
    monkeypatch.setattr("platformdirs.user_data_dir", lambda *a, **k: str(data_dir))


def test_backup_db_files_copies_db_and_wal_siblings(tmp_path, monkeypatch):
    from rekordbox_set_list_manager.services.rekordbox_db import _backup_db_files

    db_dir = tmp_path / "rekordbox"
    db_dir.mkdir()
    (db_dir / "master.db").write_bytes(b"main")
    (db_dir / "master.db-wal").write_bytes(b"wal")
    (db_dir / "master.db-shm").write_bytes(b"shm")
    data_dir = tmp_path / "userdata"
    _fake_user_data_dir(monkeypatch, data_dir)

    _backup_db_files(db_dir / "master.db")

    backup_dir = data_dir / "backups" / "rekordbox"
    names = sorted(p.name for p in backup_dir.iterdir())
    assert len(names) == 3
    assert all(name.startswith("master-") for name in names)
    assert {name.split("Z")[-1] for name in names} == {".db", ".db-wal", ".db-shm"}
    for src_name, suffix in [
        ("master.db", ".db"),
        ("master.db-wal", ".db-wal"),
        ("master.db-shm", ".db-shm"),
    ]:
        copied = next(p for p in backup_dir.iterdir() if p.name.endswith(suffix))
        assert copied.read_bytes() == (db_dir / src_name).read_bytes()


def test_backup_db_files_handles_missing_wal_siblings(tmp_path, monkeypatch):
    """Without WAL mode only master.db exists — the glob still works."""
    from rekordbox_set_list_manager.services.rekordbox_db import _backup_db_files

    db_dir = tmp_path / "rekordbox"
    db_dir.mkdir()
    (db_dir / "master.db").write_bytes(b"main")
    data_dir = tmp_path / "userdata"
    _fake_user_data_dir(monkeypatch, data_dir)

    _backup_db_files(db_dir / "master.db")

    backup_dir = data_dir / "backups" / "rekordbox"
    names = [p.name for p in backup_dir.iterdir()]
    assert len(names) == 1
    assert names[0].endswith(".db")


def test_backup_db_files_prunes_to_last_five_groups(tmp_path, monkeypatch):
    from rekordbox_set_list_manager.services.rekordbox_db import _backup_db_files

    db_dir = tmp_path / "rekordbox"
    db_dir.mkdir()
    (db_dir / "master.db").write_bytes(b"main")
    data_dir = tmp_path / "userdata"
    backup_dir = data_dir / "backups" / "rekordbox"
    backup_dir.mkdir(parents=True)
    # Six pre-existing timestamp-groups (db + wal each) → the new copy makes 7.
    for i in range(6):
        ts = f"2020010{i}T000000Z"
        (backup_dir / f"master-{ts}.db").write_bytes(b"old")
        (backup_dir / f"master-{ts}.db-wal").write_bytes(b"old-wal")
    _fake_user_data_dir(monkeypatch, data_dir)

    _backup_db_files(db_dir / "master.db")

    groups = {p.name.split(".")[0] for p in backup_dir.iterdir()}
    assert len(groups) == 5
    # The two oldest groups are pruned; the four newest old groups plus the
    # fresh one survive.
    assert "master-20200100T000000Z" not in groups
    assert "master-20200101T000000Z" not in groups
    assert "master-20200105T000000Z" in groups
