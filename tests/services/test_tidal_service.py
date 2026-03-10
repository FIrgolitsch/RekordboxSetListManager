"""Tests for TidalService using mocked tidalapi."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from set_manager.models.enums import MatchStatus, TrackSource
from set_manager.services.tidal_service import (
    TidalService,
    TidalServiceError,
    _tidal_to_track,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(
    username: str = "DJ Test",
    playlists: list | None = None,
    login_ok: bool = True,
) -> MagicMock:
    session = MagicMock()
    session.load_session_from_file.return_value = login_ok
    session.check_login.return_value = login_ok
    session.user.username = username
    session.user.id = 99
    if playlists is not None:
        session.user.playlist_and_favorite_playlists.return_value = playlists
    return session


def _make_playlist(
    playlist_id: str = "pl-uuid-1",
    name: str = "Test Playlist",
    num_tracks: int = 3,
    tracks: list | None = None,
) -> MagicMock:
    pl = MagicMock()
    pl.id = playlist_id
    pl.name = name
    pl.num_tracks = num_tracks
    if tracks is not None:
        pl.tracks.return_value = tracks
    return pl


def _make_tidal_track(
    track_id: int = 111,
    title: str = "Test Track",
    artist_name: str = "Test Artist",
    duration: int = 300,
    isrc: str | None = "USUM71234567",
    bpm: int = 0,
) -> MagicMock:
    t = MagicMock()
    t.id = track_id
    t.title = title
    t.artist.name = artist_name
    t.duration = duration
    t.isrc = isrc
    t.bpm = bpm
    return t


# ---------------------------------------------------------------------------
# authenticate
# ---------------------------------------------------------------------------


@patch("tidalapi.Session")
def test_authenticate_returns_username(mock_cls):
    mock_cls.return_value = _make_session(username="DJ Bob")

    name = TidalService().authenticate()

    assert name == "DJ Bob"


@patch("tidalapi.Session")
def test_authenticate_falls_back_to_user_id_when_no_username(mock_cls):
    session = _make_session()
    session.user.username = None
    session.user.id = 42
    mock_cls.return_value = session

    name = TidalService().authenticate()

    assert name == "42"


@patch("tidalapi.Session")
def test_authenticate_raises_when_not_completed(mock_cls):
    session = _make_session(login_ok=False)
    mock_cls.return_value = session

    with pytest.raises(TidalServiceError, match="not completed"):
        TidalService().authenticate()


@patch("tidalapi.Session")
def test_authenticate_raises_on_exception(mock_cls):
    session = MagicMock()
    session.load_session_from_file.return_value = False
    session.login_oauth_simple.side_effect = RuntimeError("disk error")
    mock_cls.return_value = session

    with pytest.raises(TidalServiceError, match="Authentication failed"):
        TidalService().authenticate()


# ---------------------------------------------------------------------------
# get_playlists
# ---------------------------------------------------------------------------


def test_get_playlists_raises_when_not_authenticated():
    with pytest.raises(TidalServiceError, match="Not authenticated"):
        TidalService().get_playlists()


@patch("tidalapi.Session")
def test_get_playlists_returns_list(mock_cls):
    pl1 = _make_playlist("id-1", "Playlist A", 10)
    pl2 = _make_playlist("id-2", "Playlist B", 5)
    mock_cls.return_value = _make_session(playlists=[pl1, pl2])

    svc = TidalService()
    svc.authenticate()
    result = svc.get_playlists()

    assert len(result) == 2
    assert result[0] == {"id": "id-1", "name": "Playlist A", "track_count": 10}
    assert result[1] == {"id": "id-2", "name": "Playlist B", "track_count": 5}


@patch("tidalapi.Session")
def test_get_playlists_raises_on_api_error(mock_cls):
    session = _make_session()
    session.user.playlist_and_favorite_playlists.side_effect = RuntimeError("API error")
    session.user.playlists.side_effect = RuntimeError("API error")
    mock_cls.return_value = session

    svc = TidalService()
    svc.authenticate()

    with pytest.raises(TidalServiceError, match="Failed to fetch playlists"):
        svc.get_playlists()


@patch("tidalapi.Session")
def test_get_playlists_falls_back_when_combined_endpoint_fails(mock_cls):
    """Falls back to separate owned + favourite calls on 500 from the combined endpoint."""
    pl_owned = _make_playlist("id-own", "My Playlist", 4)
    pl_fav = _make_playlist("id-fav", "Fav Playlist", 2)

    session = _make_session()
    session.user.playlist_and_favorite_playlists.side_effect = RuntimeError("500 Server Error")
    session.user.playlists.return_value = [pl_owned]
    session.user.favorites.playlists.return_value = [pl_fav]
    mock_cls.return_value = session

    svc = TidalService()
    svc.authenticate()
    result = svc.get_playlists()

    assert len(result) == 2
    assert result[0]["id"] == "id-own"
    assert result[1]["id"] == "id-fav"


@patch("tidalapi.Session")
def test_get_playlists_fallback_deduplicates(mock_cls):
    """Playlists appearing in both owned and favourites are not duplicated."""
    pl = _make_playlist("shared-id", "Shared", 1)
    session = _make_session()
    session.user.playlist_and_favorite_playlists.side_effect = RuntimeError("500")
    session.user.playlists.return_value = [pl]
    session.user.favorites.playlists.return_value = [pl]
    mock_cls.return_value = session

    svc = TidalService()
    svc.authenticate()
    result = svc.get_playlists()

    assert len(result) == 1


# ---------------------------------------------------------------------------
# get_playlist_tracks
# ---------------------------------------------------------------------------


def test_get_playlist_tracks_raises_when_not_authenticated():
    with pytest.raises(TidalServiceError, match="Not authenticated"):
        TidalService().get_playlist_tracks("some-id")


@patch("tidalapi.Session")
def test_get_playlist_tracks_returns_tracks(mock_cls):
    raw_track = _make_tidal_track(track_id=99, title="Hey Jude", artist_name="The Beatles")
    pl = _make_playlist(tracks=[raw_track])
    session = _make_session()
    session.playlist.return_value = pl
    mock_cls.return_value = session

    svc = TidalService()
    svc.authenticate()
    tracks = svc.get_playlist_tracks("pl-uuid")

    assert len(tracks) == 1
    t = tracks[0]
    assert t.title == "Hey Jude"
    assert t.artist == "The Beatles"
    assert t.duration == 300
    assert t.isrc == "USUM71234567"
    assert t.tidal_id == "99"
    assert t.source == TrackSource.TIDAL
    assert t.match_status == MatchStatus.UNMATCHED


@patch("tidalapi.Session")
def test_get_playlist_tracks_handles_missing_isrc(mock_cls):
    raw_track = _make_tidal_track(isrc=None)
    pl = _make_playlist(tracks=[raw_track])
    session = _make_session()
    session.playlist.return_value = pl
    mock_cls.return_value = session

    svc = TidalService()
    svc.authenticate()
    tracks = svc.get_playlist_tracks("pl-uuid")

    assert tracks[0].isrc is None


@patch("tidalapi.Session")
def test_get_playlist_tracks_raises_on_api_error(mock_cls):
    session = _make_session()
    session.playlist.side_effect = RuntimeError("not found")
    mock_cls.return_value = session

    svc = TidalService()
    svc.authenticate()

    with pytest.raises(TidalServiceError, match="Failed to fetch playlist tracks"):
        svc.get_playlist_tracks("bad-id")


# ---------------------------------------------------------------------------
# _tidal_to_track
# ---------------------------------------------------------------------------


def test_tidal_to_track_includes_bpm_when_nonzero():
    raw = _make_tidal_track(bpm=128)
    track = _tidal_to_track(raw)
    assert track.bpm == pytest.approx(128.0)


def test_tidal_to_track_excludes_bpm_when_zero():
    raw = _make_tidal_track(bpm=0)
    track = _tidal_to_track(raw)
    assert track.bpm is None


def test_tidal_to_track_empty_artist_when_none():
    raw = _make_tidal_track()
    raw.artist = None
    track = _tidal_to_track(raw)
    assert track.artist == ""
