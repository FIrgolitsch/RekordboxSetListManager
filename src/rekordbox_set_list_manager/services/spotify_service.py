"""Spotify integration service — authentication and playlist import."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import platformdirs
import spotipy
from spotipy.oauth2 import SpotifyPKCE

if TYPE_CHECKING:
    from collections.abc import Callable

from rekordbox_set_list_manager.models.enums import MatchStatus, TrackSource
from rekordbox_set_list_manager.models.track import Track
from rekordbox_set_list_manager.services._spotify_adapter import (
    SecureCacheFileHandler,
    fetch_playlist_items,
)
from rekordbox_set_list_manager.services.streaming_base import PlaylistResult, StreamingService
from rekordbox_set_list_manager.utils import config

log = logging.getLogger(__name__)

_CACHE_DIR = Path(platformdirs.user_cache_dir("rekordbox_set_list_manager"))


class SpotifyServiceError(Exception):
    """Raised when a Spotify API operation fails."""


class SpotifyService(StreamingService):
    """Wraps spotipy for playlist import.

    Call :meth:`authenticate` once before any other method.
    The instance keeps the authenticated client alive for the session.
    """

    SCOPES = (
        "playlist-read-private playlist-read-collaborative "
        "playlist-modify-public playlist-modify-private"
    )
    REDIRECT_URI = "http://127.0.0.1:8888/callback"
    _CACHE_PATH = _CACHE_DIR / ".spotify_cache"

    def __init__(self) -> None:
        """Initialise the Spotify service with no active session."""
        self._sp: spotipy.Spotify | None = None

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def authenticate(
        self,
        link_callback: Callable[[str], None] | None = None,  # noqa: ARG002 — ignored; Spotify uses browser redirect
        cancel_check: Callable[[], bool] | None = None,
    ) -> str:
        """Open browser for PKCE auth (skipped if cached token is valid).

        *link_callback* is accepted for interface compatibility but ignored —
        Spotify uses a browser redirect flow, not a device-code link.

        Parameters
        ----------
        link_callback : Callable[[str], None] | None
            Accepted for interface compatibility but ignored by Spotify's
            browser-redirect flow.  Defaults to ``None``.
        cancel_check : Callable[[], bool] | None
            Checked before and after the blocking PKCE wait — the browser
            redirect cannot be interrupted mid-wait, but a cancel request is
            honoured by discarding the result.  Defaults to ``None``.

        Returns
        -------
        str
            The authenticated user's display name.

        Raises
        ------
        SpotifyServiceError
            If ``spotify_client_id`` is not configured or authentication fails.

        """
        client_id = config.get("spotify_client_id")
        if not client_id:
            raise SpotifyServiceError(
                "Spotify Client ID not configured. Open Spotify Settings first."
            )
        if cancel_check is not None and cancel_check():
            raise SpotifyServiceError("Authentication cancelled.")

        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        auth_manager = SpotifyPKCE(
            client_id=str(client_id),
            redirect_uri=self.REDIRECT_URI,
            scope=self.SCOPES,
            cache_handler=SecureCacheFileHandler(cache_path=str(self._CACHE_PATH)),
            open_browser=True,
        )
        self._sp = spotipy.Spotify(auth_manager=auth_manager)
        try:
            user = self._sp.current_user()
        except spotipy.SpotifyException as exc:
            self._sp = None
            raise SpotifyServiceError(f"Authentication failed: {exc}") from exc

        if cancel_check is not None and cancel_check():
            self._sp = None
            raise SpotifyServiceError("Authentication cancelled.")

        return user.get("display_name") or user.get("id") or "Unknown user"

    def is_authenticated(self) -> bool:
        """Return ``True`` if a Spotify session is active.

        Returns
        -------
        bool
            ``True`` when an authenticated :class:`spotipy.Spotify` client exists.

        """
        return self._sp is not None

    # ------------------------------------------------------------------
    # Playlists
    # ------------------------------------------------------------------

    def get_playlists(self) -> list[dict]:
        """Return metadata for all of the authenticated user's playlists.

        Returns
        -------
        list[dict]
            Each entry contains ``"id"`` (str) and ``"name"`` (str) keys.

        Raises
        ------
        SpotifyServiceError
            If not authenticated or the API call fails.

        """
        sp = self._require_auth()
        playlists: list[dict] = []
        try:
            page = sp.current_user_playlists(limit=50)
            while page:
                for item in page["items"]:
                    if item is None:
                        continue
                    playlists.append(
                        {
                            "id": item["id"],
                            "name": item["name"],
                        }
                    )
                page = sp.next(page) if page.get("next") else None
        except spotipy.SpotifyException as exc:
            raise SpotifyServiceError(f"Failed to fetch playlists: {exc}") from exc

        return playlists

    # ------------------------------------------------------------------
    # Tracks
    # ------------------------------------------------------------------

    def get_playlist_tracks(self, playlist_id: str) -> PlaylistResult:
        """Fetch all tracks from *playlist_id* and return as :class:`Track` objects.

        Parameters
        ----------
        playlist_id : str
            The Spotify playlist ID to fetch tracks from.

        Returns
        -------
        PlaylistResult
            ``tracks`` holds the importable tracks — each with
            ``source=SPOTIFY``, ``spotify_id``, ``isrc`` (if available), and
            ``match_status=UNMATCHED``.  ``skipped`` holds a
            :class:`SkippedItem` per dropped entry (null slots, episodes,
            nameless tracks).

        Raises
        ------
        SpotifyServiceError
            If not authenticated or the API call fails.

        """
        sp = self._require_auth()
        try:
            track_dicts, skipped = fetch_playlist_items(sp, playlist_id)
        except spotipy.SpotifyException as exc:
            raise SpotifyServiceError(f"Failed to fetch playlist tracks: {exc}") from exc

        return PlaylistResult(
            tracks=[_item_to_track(data) for data in track_dicts],
            skipped=skipped,
        )

    def replace_playlist_tracks(self, playlist_id: str, spotify_uris: list[str]) -> str:
        """Replace all tracks in *playlist_id* with *spotify_uris* in order.

        Parameters
        ----------
        playlist_id : str
            The Spotify playlist ID to update.
        spotify_uris : list[str]
            Ordered list of Spotify track URIs to set as the playlist contents.

        Returns
        -------
        str
            The ``snapshot_id`` returned by the Spotify API.

        Raises
        ------
        SpotifyServiceError
            If not authenticated or the API call fails.

        """
        sp = self._require_auth()
        try:
            result = sp.playlist_replace_items(playlist_id, spotify_uris)
        except spotipy.SpotifyException as exc:
            raise SpotifyServiceError(f"Failed to update playlist order: {exc}") from exc
        return result.get("snapshot_id", "")

    def try_silent_authenticate(self) -> str | None:
        """Authenticate using a cached token without opening the browser.

        Returns
        -------
        str | None
            The display name on success, or ``None`` if no valid cached token
            exists or authentication fails.

        """
        client_id = config.get("spotify_client_id")
        if not client_id or not self._CACHE_PATH.exists():
            return None
        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            auth_manager = SpotifyPKCE(
                client_id=str(client_id),
                redirect_uri=self.REDIRECT_URI,
                scope=self.SCOPES,
                cache_handler=SecureCacheFileHandler(cache_path=str(self._CACHE_PATH)),
                open_browser=False,
            )
            self._sp = spotipy.Spotify(auth_manager=auth_manager)
            user = self._sp.current_user()
            return user.get("display_name") or user.get("id") or "Unknown user"
        except Exception:
            log.warning("Cached Spotify session could not be restored", exc_info=True)
            self._sp = None
            return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_auth(self) -> spotipy.Spotify:
        if self._sp is None:
            raise SpotifyServiceError("Not authenticated. Call authenticate() first.")
        return self._sp


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _item_to_track(data: dict) -> Track:
    """Convert a Spotify track dict to a :class:`Track`."""
    artists = ", ".join(a.get("name", "") for a in (data.get("artists") or []))
    duration_ms: int | None = data.get("duration_ms")
    duration = duration_ms // 1000 if duration_ms is not None else None
    isrc: str | None = (data.get("external_ids") or {}).get("isrc")
    return Track(
        title=data["name"],
        artist=artists,
        duration=duration,
        isrc=isrc or None,
        spotify_id=data.get("id"),  # None for local files
        source=TrackSource.SPOTIFY,
        match_status=MatchStatus.UNMATCHED,
    )
