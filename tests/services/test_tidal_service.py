"""Tests for TidalService using mocked tidalapi."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import rekordbox_set_list_manager.services.tidal_service as svc_mod
import rekordbox_set_list_manager.utils.config as config_mod
from rekordbox_set_list_manager.models.enums import MatchStatus, TrackSource
from rekordbox_set_list_manager.services import _tidal_download_adapter
from rekordbox_set_list_manager.services.streaming_base import PlaylistResult
from rekordbox_set_list_manager.services.tidal_service import (
    TidalService,
    TidalServiceError,
    _tidal_to_track,
)

_TEST_CLIENT_ID = "test-client-id"
_TEST_CLIENT_SECRET = "test-client-secret"  # noqa: S105 — fixture value, not a real secret


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate config and run every test under a fixed credential pair.

    The pre-existing suite predates the credential requirement, so it runs
    under a fixed env pair plus the matching stored minting-client
    fingerprint — the credential and fingerprint gates stay transparent to
    it.  Tests that exercise those gates override env/config in the test
    body.  ``_SESSION_FILE`` is redirected too so no test can touch the real
    cached session file.
    """
    config_dir = tmp_path / "config"
    monkeypatch.setattr(config_mod, "_CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_mod, "_CONFIG_PATH", config_dir / "config.json")
    monkeypatch.setattr(config_mod, "_cache", None)
    monkeypatch.delenv("SET_MANAGER_TIDAL_CLIENT_ID", raising=False)
    monkeypatch.delenv("SET_MANAGER_TIDAL_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("SET_MANAGER_TIDAL_CLIENT_ID", _TEST_CLIENT_ID)
    monkeypatch.setenv("SET_MANAGER_TIDAL_CLIENT_SECRET", _TEST_CLIENT_SECRET)
    monkeypatch.setattr(svc_mod, "_SESSION_FILE", tmp_path / "tidal_session.json")
    config_mod.set_value(
        "tidal_session_client", _tidal_download_adapter.client_fingerprint(_TEST_CLIENT_ID)
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
def test_authenticate_logs_warning_when_session_load_fails(mock_cls, caplog):
    """A failed session restore falls back to login but is logged with traceback."""
    session = _make_session()
    session.load_session_from_file.side_effect = RuntimeError("corrupt file")
    mock_cls.return_value = session

    with caplog.at_level(logging.WARNING, logger=svc_mod.__name__):
        name = TidalService().authenticate()

    assert name == "DJ Test"
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) >= 1
    assert all(r.exc_info for r in warnings)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file permissions")
@patch("tidalapi.Session")
def test_authenticate_chmods_session_file_after_save(mock_cls, tmp_path, monkeypatch):
    """The session file is restricted to owner-only right after it is written."""
    session_file = tmp_path / "tidal_session.json"
    monkeypatch.setattr(svc_mod, "_SESSION_FILE", session_file)
    session = _make_session(login_ok=False)
    session.check_login.return_value = True  # login completes after the save
    session.save_session_to_file.side_effect = lambda path: Path(path).write_text("{}")
    mock_cls.return_value = session

    TidalService().authenticate()

    assert (session_file.stat().st_mode & 0o777) == 0o600


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file permissions")
@patch("tidalapi.Session")
def test_authenticate_chmods_loose_existing_session_file(mock_cls, tmp_path, monkeypatch):
    """A pre-existing world-readable session file is tightened on load."""
    session_file = tmp_path / "tidal_session.json"
    session_file.write_text("{}")
    session_file.chmod(0o644)
    monkeypatch.setattr(svc_mod, "_SESSION_FILE", session_file)
    mock_cls.return_value = _make_session()

    name = TidalService().authenticate()

    assert name == "DJ Test"
    assert (session_file.stat().st_mode & 0o777) == 0o600


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
    result = svc.get_playlist_tracks("pl-uuid")

    assert isinstance(result, PlaylistResult)
    assert result.skipped == []
    assert len(result.tracks) == 1
    t = result.tracks[0]
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
    result = svc.get_playlist_tracks("pl-uuid")

    assert result.tracks[0].isrc is None


@patch("tidalapi.Session")
def test_get_playlist_tracks_raises_on_api_error(mock_cls):
    session = _make_session()
    session.playlist.side_effect = RuntimeError("not found")
    mock_cls.return_value = session

    svc = TidalService()
    svc.authenticate()

    with pytest.raises(TidalServiceError, match="Failed to fetch playlist tracks"):
        svc.get_playlist_tracks("bad-id")


@patch("tidalapi.Session")
def test_get_playlist_tracks_paginates_until_short_batch(mock_cls):
    """Pagination ignores num_tracks: pages are fetched until a short batch arrives."""
    page1 = [_make_tidal_track(track_id=i) for i in range(100)]
    page2 = [_make_tidal_track(track_id=100 + i) for i in range(30)]
    pl = _make_playlist(num_tracks=250)  # over-reports the total
    pl.tracks.side_effect = [page1, page2]
    session = _make_session()
    session.playlist.return_value = pl
    mock_cls.return_value = session

    svc = TidalService()
    svc.authenticate()
    result = svc.get_playlist_tracks("pl-uuid")

    assert len(result.tracks) == 130
    assert pl.tracks.call_count == 2
    second_call = pl.tracks.call_args_list[1]
    assert second_call.kwargs["offset"] == 100


@patch("tidalapi.Session")
def test_get_playlist_tracks_fetches_when_num_tracks_unknown(mock_cls):
    """num_tracks = -1 (tidalapi's 'unknown' default) must not suppress the fetch."""
    page = [_make_tidal_track(track_id=i) for i in range(5)]
    pl = _make_playlist(num_tracks=-1)
    pl.tracks.side_effect = [page]  # short batch ends pagination
    session = _make_session()
    session.playlist.return_value = pl
    mock_cls.return_value = session

    svc = TidalService()
    svc.authenticate()
    result = svc.get_playlist_tracks("pl-uuid")

    assert len(result.tracks) == 5
    assert pl.tracks.call_count == 1


@patch("tidalapi.Session")
def test_get_playlist_tracks_continues_past_empty_first_page_when_short_check(
    mock_cls,
):
    """An empty batch ends pagination regardless of the reported total."""
    pl = _make_playlist(num_tracks=500)
    pl.tracks.side_effect = [[]]
    session = _make_session()
    session.playlist.return_value = pl
    mock_cls.return_value = session

    svc = TidalService()
    svc.authenticate()
    result = svc.get_playlist_tracks("pl-uuid")

    assert result.tracks == []
    assert result.skipped == []


@patch("tidalapi.Session")
def test_get_playlist_tracks_reports_unconvertible_items(mock_cls):
    """A malformed playlist item is skipped with a label and reason, not raised."""
    good = _make_tidal_track(track_id=1, title="Good Track")
    bad = object()  # no title/artist attributes — conversion fails
    pl = _make_playlist()
    pl.tracks.side_effect = [[good, bad]]
    session = _make_session()
    session.playlist.return_value = pl
    mock_cls.return_value = session

    svc = TidalService()
    svc.authenticate()
    result = svc.get_playlist_tracks("pl-uuid")

    assert [t.title for t in result.tracks] == ["Good Track"]
    assert len(result.skipped) == 1
    assert result.skipped[0].label == "Track at position 2"
    assert result.skipped[0].reason


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


# ---------------------------------------------------------------------------
# try_silent_authenticate
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file permissions")
@patch("tidalapi.Session")
def test_try_silent_authenticate_chmods_session_file(mock_cls, tmp_path, monkeypatch):
    """Silent restore tightens a pre-existing loose session file."""
    session_file = tmp_path / "tidal_session.json"
    session_file.write_text("{}")
    session_file.chmod(0o644)
    monkeypatch.setattr(svc_mod, "_SESSION_FILE", session_file)
    mock_cls.return_value = _make_session()

    name = TidalService().try_silent_authenticate()

    assert name == "DJ Test"
    assert (session_file.stat().st_mode & 0o777) == 0o600


# ---------------------------------------------------------------------------
# Credential wiring — the user-supplied client pair must land on the session
# before any session-file load or login, and the cached session file is bound
# to the client that minted it via the ``tidal_session_client`` fingerprint.
# ---------------------------------------------------------------------------


def _existing_session_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a cached-session file inside the isolated tmp dir."""
    session_file = tmp_path / "tidal_session.json"
    session_file.write_text("{}")
    monkeypatch.setattr(svc_mod, "_SESSION_FILE", session_file)
    return session_file


@patch("tidalapi.Session")
def test_authenticate_applies_env_credentials_before_session_load(mock_cls, monkeypatch):
    """The resolved client pair lands on session.config before any session I/O."""
    monkeypatch.setenv("SET_MANAGER_TIDAL_CLIENT_ID", "env-id")
    monkeypatch.setenv("SET_MANAGER_TIDAL_CLIENT_SECRET", "env-secret")
    session = _make_session()
    seen = {}

    def _load(_path):
        seen["client_id"] = session.config.client_id
        seen["client_secret"] = session.config.client_secret
        return True

    session.load_session_from_file.side_effect = _load
    mock_cls.return_value = session

    assert TidalService().authenticate() == "DJ Test"
    assert seen == {"client_id": "env-id", "client_secret": "env-secret"}


@patch("tidalapi.Session")
def test_authenticate_env_credentials_beat_config(mock_cls, monkeypatch):
    """The env pair wins when both env and config.json carry credentials."""
    config_mod.set_value("tidal_client_id", "cfg-id")
    config_mod.set_value("tidal_client_secret", "cfg-secret")
    monkeypatch.setenv("SET_MANAGER_TIDAL_CLIENT_ID", "env-id")
    monkeypatch.setenv("SET_MANAGER_TIDAL_CLIENT_SECRET", "env-secret")
    session = _make_session()
    seen = {}

    def _load(_path):
        seen["client_id"] = session.config.client_id
        seen["client_secret"] = session.config.client_secret
        return True

    session.load_session_from_file.side_effect = _load
    mock_cls.return_value = session

    TidalService().authenticate()

    assert seen == {"client_id": "env-id", "client_secret": "env-secret"}


@patch("tidalapi.Session")
def test_authenticate_falls_back_to_config_credentials(mock_cls, monkeypatch):
    """With no env pair, the config.json pair is applied to the session."""
    monkeypatch.delenv("SET_MANAGER_TIDAL_CLIENT_ID", raising=False)
    monkeypatch.delenv("SET_MANAGER_TIDAL_CLIENT_SECRET", raising=False)
    config_mod.set_value("tidal_client_id", "cfg-id")
    config_mod.set_value("tidal_client_secret", "cfg-secret")
    session = _make_session()
    seen = {}

    def _load(_path):
        seen["client_id"] = session.config.client_id
        seen["client_secret"] = session.config.client_secret
        return True

    session.load_session_from_file.side_effect = _load
    mock_cls.return_value = session

    TidalService().authenticate()

    assert seen == {"client_id": "cfg-id", "client_secret": "cfg-secret"}


@patch("tidalapi.Session")
def test_authenticate_without_credentials_uses_embedded_pair(mock_cls, monkeypatch):
    """No configured pair keeps tidalapi's embedded credentials — auth just works."""
    monkeypatch.delenv("SET_MANAGER_TIDAL_CLIENT_ID", raising=False)
    monkeypatch.delenv("SET_MANAGER_TIDAL_CLIENT_SECRET", raising=False)
    session = _make_session()
    session.config.client_id = "embedded-id"
    session.config.client_secret = "embedded-secret"  # noqa: S105 — fixture value
    mock_cls.return_value = session

    assert TidalService().authenticate() == "DJ Test"
    assert session.config.client_id == "embedded-id"
    assert session.config.client_secret == "embedded-secret"  # noqa: S105


@patch("tidalapi.Session")
def test_authenticate_matching_fingerprint_keeps_cached_session(mock_cls, tmp_path, monkeypatch):
    """A session minted under the current client loads without re-auth."""
    session_file = _existing_session_file(tmp_path, monkeypatch)
    session = _make_session()
    mock_cls.return_value = session

    assert TidalService().authenticate() == "DJ Test"
    assert session_file.exists()
    session.load_session_from_file.assert_called_once()
    session.login_oauth_simple.assert_not_called()
    session.login_oauth.assert_not_called()


@patch("tidalapi.Session")
def test_authenticate_mismatched_fingerprint_discards_session_and_reauths(
    mock_cls, tmp_path, monkeypatch
):
    """A session minted under another client is deleted before load and re-authed."""
    session_file = _existing_session_file(tmp_path, monkeypatch)
    config_mod.set_value("tidal_session_client", "fingerprint-of-another-client")
    session = _make_session(login_ok=False)
    session.check_login.return_value = True
    session.save_session_to_file.side_effect = lambda path: Path(path).write_text("{}")
    seen = {}

    def _load(_path):
        seen["file_existed_at_load"] = session_file.exists()
        return False

    session.load_session_from_file.side_effect = _load
    mock_cls.return_value = session

    assert TidalService().authenticate() == "DJ Test"
    assert seen["file_existed_at_load"] is False
    session.login_oauth_simple.assert_called_once()
    assert config_mod.get("tidal_session_client") == (
        _tidal_download_adapter.client_fingerprint(_TEST_CLIENT_ID)
    )


@patch("tidalapi.Session")
def test_authenticate_unfingerprinted_session_is_discarded(mock_cls, tmp_path, monkeypatch):
    """A pre-existing session file with no recorded fingerprint re-authenticates."""
    session_file = _existing_session_file(tmp_path, monkeypatch)
    config_mod._cache.pop("tidal_session_client", None)
    session = _make_session(login_ok=False)
    session.check_login.return_value = True
    session.save_session_to_file.side_effect = lambda path: Path(path).write_text("{}")
    seen = {}

    def _load(_path):
        seen["file_existed_at_load"] = session_file.exists()
        return False

    session.load_session_from_file.side_effect = _load
    mock_cls.return_value = session

    assert TidalService().authenticate() == "DJ Test"
    assert seen["file_existed_at_load"] is False
    session.login_oauth_simple.assert_called_once()


@patch("tidalapi.Session")
def test_authenticate_records_client_fingerprint_after_fresh_login(mock_cls, tmp_path, monkeypatch):
    """A fresh device-code login stores the minting client's fingerprint."""
    monkeypatch.setattr(svc_mod, "_SESSION_FILE", tmp_path / "tidal_session.json")
    monkeypatch.setenv("SET_MANAGER_TIDAL_CLIENT_ID", "env-id")
    monkeypatch.setenv("SET_MANAGER_TIDAL_CLIENT_SECRET", "env-secret")
    session = _make_session(login_ok=False)
    session.check_login.return_value = True
    session.save_session_to_file.side_effect = lambda path: Path(path).write_text("{}")
    mock_cls.return_value = session

    assert TidalService().authenticate() == "DJ Test"
    assert config_mod.get("tidal_session_client") == (
        _tidal_download_adapter.client_fingerprint("env-id")
    )


@patch("tidalapi.Session")
def test_try_silent_authenticate_without_credentials_discards_mismatched_file(
    mock_cls, tmp_path, monkeypatch
):
    """With no configured pair, a file minted under another client is discarded."""
    session_file = _existing_session_file(tmp_path, monkeypatch)
    monkeypatch.delenv("SET_MANAGER_TIDAL_CLIENT_ID", raising=False)
    monkeypatch.delenv("SET_MANAGER_TIDAL_CLIENT_SECRET", raising=False)
    session = _make_session()
    session.config.client_id = "embedded-id"
    session.config.client_secret = "embedded-secret"  # noqa: S105 — fixture value
    mock_cls.return_value = session

    assert TidalService().try_silent_authenticate() is None
    assert not session_file.exists()
    session.load_session_from_file.assert_not_called()


@patch("tidalapi.Session")
def test_try_silent_authenticate_embedded_pair_restores_matching_file(
    mock_cls, tmp_path, monkeypatch
):
    """A session minted under the embedded client restores with no configured pair."""
    _existing_session_file(tmp_path, monkeypatch)
    monkeypatch.delenv("SET_MANAGER_TIDAL_CLIENT_ID", raising=False)
    monkeypatch.delenv("SET_MANAGER_TIDAL_CLIENT_SECRET", raising=False)
    config_mod.set_value(
        "tidal_session_client", _tidal_download_adapter.client_fingerprint("embedded-id")
    )
    session = _make_session()
    session.config.client_id = "embedded-id"
    session.config.client_secret = "embedded-secret"  # noqa: S105 — fixture value
    mock_cls.return_value = session

    assert TidalService().try_silent_authenticate() == "DJ Test"


@patch("tidalapi.Session")
def test_try_silent_authenticate_mismatched_fingerprint_deletes_file(
    mock_cls, tmp_path, monkeypatch
):
    """A session minted under another client is deleted and not restored."""
    session_file = _existing_session_file(tmp_path, monkeypatch)
    config_mod.set_value("tidal_session_client", "fingerprint-of-another-client")
    session = _make_session()
    mock_cls.return_value = session

    assert TidalService().try_silent_authenticate() is None
    assert not session_file.exists()
    session.load_session_from_file.assert_not_called()


@patch("tidalapi.Session")
def test_try_silent_authenticate_matching_fingerprint_restores(mock_cls, tmp_path, monkeypatch):
    """A session minted under the current client restores silently."""
    session_file = _existing_session_file(tmp_path, monkeypatch)
    mock_cls.return_value = _make_session()

    assert TidalService().try_silent_authenticate() == "DJ Test"
    assert session_file.exists()
