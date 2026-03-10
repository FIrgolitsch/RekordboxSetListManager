"""Tests for RekordboxDbService using mocked pyrekordbox."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from set_manager.models.enums import MatchStatus, RekordboxColor, TrackSource
from set_manager.services.rekordbox_db import RekordboxDbError, RekordboxDbService, _parse_color

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_content(
    track_id: str = "1001",
    title: str = "Test Track",
    artist_name: str = "Test Artist",
    bpm: int | None = 12800,  # stored as BPM × 100 (128.00 → 12800)
    length: int = 240,
    key_name: str = "8A",
    folder_path: str = "/music/test.mp3",
    isrc: str | None = "USABC1234567",
    color_name: str | None = "Blue",
    service_id: int = 0,
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
def test_get_collection_skips_tracks_without_filepath(mock_cls):
    no_path = _make_content(track_id="1", title="No Path Track", folder_path=None)
    with_path = _make_content(track_id="2", title="With Path Track")
    mock_cls.return_value = _make_db([no_path, with_path])

    tracks = RekordboxDbService().get_collection()

    assert len(tracks) == 1
    assert tracks[0].title == "With Path Track"


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
# find_track_by_isrc
# ---------------------------------------------------------------------------


@patch("pyrekordbox.Rekordbox6Database")
def test_find_track_by_isrc_returns_track_when_found(mock_cls):
    content = _make_content(isrc="DEBL41500001")
    db = MagicMock()
    db.__enter__ = lambda self: self
    db.__exit__ = MagicMock(return_value=False)
    query = MagicMock()
    query.first.return_value = content
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
    query.first.return_value = None
    db.get_content.return_value = query
    mock_cls.return_value = db

    assert RekordboxDbService().find_track_by_isrc("NOTFOUND") is None


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
