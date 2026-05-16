"""Tests for SpotifyService using mocked spotipy responses."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import rekordbox_set_list_manager.services.spotify_service as svc_mod
import rekordbox_set_list_manager.utils.config as cfg_module
from rekordbox_set_list_manager.models.enums import MatchStatus, TrackSource
from rekordbox_set_list_manager.services.spotify_service import SpotifyService, SpotifyServiceError
from rekordbox_set_list_manager.utils import config


@pytest.fixture(autouse=True)
def _clear_config(tmp_path, monkeypatch):
    """Redirect config writes to a temp dir and clear cache."""
    monkeypatch.setattr(cfg_module, "_CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(cfg_module, "_CONFIG_DIR", tmp_path)
    cfg_module._cache = None
    yield
    cfg_module._cache = None


@pytest.fixture
def service():
    return SpotifyService()


# ---------------------------------------------------------------------------
# authenticate
# ---------------------------------------------------------------------------


def test_authenticate_raises_when_client_id_missing(service):
    with pytest.raises(SpotifyServiceError, match="Client ID not configured"):
        service.authenticate()


@patch("rekordbox_set_list_manager.services.spotify_service.SpotifyPKCE")
@patch("rekordbox_set_list_manager.services.spotify_service.spotipy.Spotify")
def test_authenticate_returns_display_name(mock_spotify_cls, mock_pkce_cls, service):
    config.set_value("spotify_client_id", "test-client-id")
    mock_sp = MagicMock()
    mock_sp.current_user.return_value = {"display_name": "DJ Test", "id": "user123"}
    mock_spotify_cls.return_value = mock_sp

    name = service.authenticate()

    assert name == "DJ Test"
    mock_pkce_cls.assert_called_once()
    pkce_kwargs = mock_pkce_cls.call_args.kwargs
    assert pkce_kwargs["client_id"] == "test-client-id"
    assert pkce_kwargs["scope"] == SpotifyService.SCOPES


@patch("rekordbox_set_list_manager.services.spotify_service.SpotifyPKCE")
@patch("rekordbox_set_list_manager.services.spotify_service.spotipy.Spotify")
def test_authenticate_falls_back_to_id_when_no_display_name(
    mock_spotify_cls, mock_pkce_cls, service
):
    config.set_value("spotify_client_id", "test-client-id")
    mock_sp = MagicMock()
    mock_sp.current_user.return_value = {"display_name": None, "id": "user123"}
    mock_spotify_cls.return_value = mock_sp

    name = service.authenticate()
    assert name == "user123"


# ---------------------------------------------------------------------------
# get_playlists
# ---------------------------------------------------------------------------


def test_get_playlists_raises_when_not_authenticated(service):
    with pytest.raises(SpotifyServiceError, match="Not authenticated"):
        service.get_playlists()


@patch("rekordbox_set_list_manager.services.spotify_service.SpotifyPKCE")
@patch("rekordbox_set_list_manager.services.spotify_service.spotipy.Spotify")
def test_get_playlists_returns_list(mock_spotify_cls, mock_pkce_cls, service):
    config.set_value("spotify_client_id", "test-client-id")
    mock_sp = MagicMock()
    mock_sp.current_user.return_value = {"display_name": "Test", "id": "u"}
    mock_sp.current_user_playlists.return_value = {
        "items": [
            {"id": "pl1", "name": "Playlist One"},
            {"id": "pl2", "name": "Playlist Two"},
        ],
        "next": None,
    }
    mock_spotify_cls.return_value = mock_sp
    service.authenticate()

    playlists = service.get_playlists()

    assert len(playlists) == 2
    assert playlists[0] == {"id": "pl1", "name": "Playlist One"}
    assert playlists[1] == {"id": "pl2", "name": "Playlist Two"}


# ---------------------------------------------------------------------------
# get_playlist_tracks
# ---------------------------------------------------------------------------


def _make_spotify_item(
    track_id: str,
    name: str,
    artists: list[str],
    duration_ms: int = 240000,
    isrc: str | None = "USABC1234567",
) -> dict:
    """Playlist item using the current Spotify API format (track data under 'item' key)."""
    return {
        "item": {
            "id": track_id,
            "name": name,
            "type": "track",
            "duration_ms": duration_ms,
            "artists": [{"name": a} for a in artists],
            "external_ids": {"isrc": isrc} if isrc else {},
        }
    }


@patch("rekordbox_set_list_manager.services.spotify_service.SpotifyPKCE")
@patch("rekordbox_set_list_manager.services.spotify_service.spotipy.Spotify")
def test_get_playlist_tracks_returns_tracks(mock_spotify_cls, mock_pkce_cls, service):
    config.set_value("spotify_client_id", "test-client-id")
    mock_sp = MagicMock()
    mock_sp.current_user.return_value = {"display_name": "Test", "id": "u"}
    mock_sp._get.return_value = {
        "items": [
            _make_spotify_item("id1", "Hey Jude", ["The Beatles"], isrc="GBAYE6800012"),
            _make_spotify_item("id2", "Bohemian Rhapsody", ["Queen"], isrc="GBUM71029604"),
        ],
        "next": None,
    }
    mock_spotify_cls.return_value = mock_sp
    service.authenticate()

    tracks, skipped = service.get_playlist_tracks("some-playlist-id")

    assert len(tracks) == 2
    assert skipped == 0
    t = tracks[0]
    assert t.title == "Hey Jude"
    assert t.artist == "The Beatles"
    assert t.spotify_id == "id1"
    assert t.isrc == "GBAYE6800012"
    assert t.source == TrackSource.SPOTIFY
    assert t.match_status == MatchStatus.UNMATCHED
    assert t.duration == 240  # 240000 ms → 240 s


@patch("rekordbox_set_list_manager.services.spotify_service.SpotifyPKCE")
@patch("rekordbox_set_list_manager.services.spotify_service.spotipy.Spotify")
def test_get_playlist_tracks_handles_missing_isrc(mock_spotify_cls, mock_pkce_cls, service):
    config.set_value("spotify_client_id", "test-client-id")
    mock_sp = MagicMock()
    mock_sp.current_user.return_value = {"display_name": "Test", "id": "u"}
    mock_sp._get.return_value = {
        "items": [_make_spotify_item("id3", "No ISRC Track", ["Artist"], isrc=None)],
        "next": None,
    }
    mock_spotify_cls.return_value = mock_sp
    service.authenticate()

    tracks, skipped = service.get_playlist_tracks("pl")
    assert len(tracks) == 1
    assert skipped == 0
    assert tracks[0].isrc is None


@patch("rekordbox_set_list_manager.services.spotify_service.SpotifyPKCE")
@patch("rekordbox_set_list_manager.services.spotify_service.spotipy.Spotify")
def test_get_playlist_tracks_skips_null_and_episode_entries(
    mock_spotify_cls, mock_pkce_cls, service
):
    """Null track slots and podcast episodes are silently skipped."""
    config.set_value("spotify_client_id", "test-client-id")
    mock_sp = MagicMock()
    mock_sp.current_user.return_value = {"display_name": "Test", "id": "u"}
    mock_sp._get.return_value = {
        "items": [
            None,
            {"item": None},
            {"item": {"id": "ep1", "name": "Podcast Episode", "type": "episode"}},
            _make_spotify_item("id4", "Valid Track", ["Artist"]),
        ],
        "next": None,
    }
    mock_spotify_cls.return_value = mock_sp
    service.authenticate()

    tracks, skipped = service.get_playlist_tracks("pl")
    assert len(tracks) == 1
    assert skipped == 3
    assert tracks[0].title == "Valid Track"


@patch("rekordbox_set_list_manager.services.spotify_service.SpotifyPKCE")
@patch("rekordbox_set_list_manager.services.spotify_service.spotipy.Spotify")
def test_get_playlist_tracks_imports_local_files(mock_spotify_cls, mock_pkce_cls, service):
    """Tracks without a Spotify ID (local files) are imported with spotify_id=None."""
    config.set_value("spotify_client_id", "test-client-id")
    mock_sp = MagicMock()
    mock_sp.current_user.return_value = {"display_name": "Test", "id": "u"}
    mock_sp._get.return_value = {
        "items": [
            {
                "item": {
                    "id": None,
                    "name": "Local File",
                    "type": "track",
                    "artists": [{"name": "Artist"}],
                    "duration_ms": 180000,
                }
            },
            _make_spotify_item("id5", "Stream Track", ["Artist"]),
        ],
        "next": None,
    }
    mock_spotify_cls.return_value = mock_sp
    service.authenticate()

    tracks, skipped = service.get_playlist_tracks("pl")
    assert len(tracks) == 2
    assert skipped == 0
    assert tracks[0].title == "Local File"
    assert tracks[0].spotify_id is None
    assert tracks[1].spotify_id == "id5"


@patch("rekordbox_set_list_manager.services.spotify_service.SpotifyPKCE")
@patch("rekordbox_set_list_manager.services.spotify_service.spotipy.Spotify")
def test_get_playlist_tracks_supports_legacy_track_key(mock_spotify_cls, mock_pkce_cls, service):
    """Older API responses that use 'track' instead of 'item' are still handled."""
    config.set_value("spotify_client_id", "test-client-id")
    mock_sp = MagicMock()
    mock_sp.current_user.return_value = {"display_name": "Test", "id": "u"}
    mock_sp._get.return_value = {
        "items": [
            {
                "track": {
                    "id": "id6",
                    "name": "Legacy Track",
                    "type": "track",
                    "artists": [{"name": "Old Artist"}],
                    "duration_ms": 200000,
                }
            },
        ],
        "next": None,
    }
    mock_spotify_cls.return_value = mock_sp
    service.authenticate()

    tracks, skipped = service.get_playlist_tracks("pl")
    assert len(tracks) == 1
    assert skipped == 0
    assert tracks[0].title == "Legacy Track"
    assert tracks[0].spotify_id == "id6"


# ---------------------------------------------------------------------------
# try_silent_authenticate
# ---------------------------------------------------------------------------


def test_try_silent_authenticate_returns_none_without_client_id(service):
    assert service.try_silent_authenticate() is None


def test_try_silent_authenticate_returns_none_without_cache_file(service, tmp_path, monkeypatch):
    monkeypatch.setattr(svc_mod.SpotifyService, "_CACHE_PATH", tmp_path / "no_such_file")
    config.set_value("spotify_client_id", "test-client-id")
    assert service.try_silent_authenticate() is None


@patch("rekordbox_set_list_manager.services.spotify_service.SpotifyPKCE")
@patch("rekordbox_set_list_manager.services.spotify_service.spotipy.Spotify")
def test_try_silent_authenticate_returns_display_name(
    mock_spotify_cls, mock_pkce_cls, service, tmp_path, monkeypatch
):
    # Point cache path into tmp_path so the existence check passes
    cache_file = tmp_path / ".spotify_cache"
    cache_file.write_text("{}")
    monkeypatch.setattr(svc_mod.SpotifyService, "_CACHE_PATH", cache_file)

    config.set_value("spotify_client_id", "test-client-id")
    mock_sp = MagicMock()
    mock_sp.current_user.return_value = {"display_name": "Silent User", "id": "u2"}
    mock_spotify_cls.return_value = mock_sp

    name = service.try_silent_authenticate()

    assert name == "Silent User"
    pkce_kwargs = mock_pkce_cls.call_args.kwargs
    assert pkce_kwargs["open_browser"] is False
