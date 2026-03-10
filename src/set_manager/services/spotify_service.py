"""Spotify integration service — authentication and playlist import."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import platformdirs
import spotipy
from spotipy.oauth2 import SpotifyPKCE

from set_manager.models.enums import MatchStatus, TrackSource
from set_manager.models.track import Track
from set_manager.utils import config

if TYPE_CHECKING:
    pass

_CACHE_DIR = Path(platformdirs.user_cache_dir("set_manager"))


class SpotifyServiceError(Exception):
    """Raised when a Spotify API operation fails."""


class SpotifyService:
    """Wraps spotipy for playlist import.

    Call :meth:`authenticate` once before any other method.
    The instance keeps the authenticated client alive for the session.
    """

    SCOPES = "playlist-read-private playlist-read-collaborative"
    REDIRECT_URI = "http://127.0.0.1:8888/callback"
    _CACHE_PATH = _CACHE_DIR / ".spotify_cache"

    def __init__(self) -> None:
        self._sp: spotipy.Spotify | None = None

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def authenticate(self) -> str:
        """Open browser for PKCE auth (skipped if cached token is valid).

        Returns the authenticated user's display name.
        Raises :class:`SpotifyServiceError` if client_id is not set or auth fails.
        """
        client_id = config.get("spotify_client_id")
        if not client_id:
            raise SpotifyServiceError(
                "Spotify Client ID not configured. Open Spotify Settings first."
            )

        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        auth_manager = SpotifyPKCE(
            client_id=str(client_id),
            redirect_uri=self.REDIRECT_URI,
            scope=self.SCOPES,
            cache_path=str(self._CACHE_PATH),
            open_browser=True,
        )
        self._sp = spotipy.Spotify(auth_manager=auth_manager)
        try:
            user = self._sp.current_user()
        except spotipy.SpotifyException as exc:
            self._sp = None
            raise SpotifyServiceError(f"Authentication failed: {exc}") from exc

        return user.get("display_name") or user.get("id") or "Unknown user"

    # ------------------------------------------------------------------
    # Playlists
    # ------------------------------------------------------------------

    def get_playlists(self) -> list[dict]:
        """Return metadata for all of the authenticated user's playlists.

        Returns a list of ``{"id": str, "name": str}``.
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

    def get_playlist_tracks(self, playlist_id: str) -> tuple[list[Track], int]:
        """Fetch all tracks from *playlist_id* and return as :class:`Track` objects.

        Returns ``(tracks, skipped)`` where *skipped* is the count of items that
        could not be imported (local files without a Spotify ID, null entries, etc.).
        Each track has ``source=SPOTIFY``, ``spotify_id``, ``isrc`` (if available),
        and ``match_status=UNMATCHED``.
        """
        sp = self._require_auth()
        tracks: list[Track] = []
        skipped = 0
        try:
            # Use sp._get() directly to avoid spotipy's additional_types default
            # ("track,episode"), which causes the Spotify API to nullify track fields
            # for some playlists. Without additional_types only track items are returned.
            offset = 0
            while True:
                page = sp._get(
                    f"playlists/{playlist_id}/items",
                    limit=100,
                    offset=offset,
                    market="from_token",
                )
                items = page.get("items") or []
                if offset == 0 and not items:
                    break
                for item in items:
                    # Spotify API (post-2025) returns track data under "item";
                    # older responses used "track". Try "item" first.
                    track_data = (item.get("item") or item.get("track")) if item else None
                    if not track_data or track_data.get("type") == "episode":
                        skipped += 1
                        continue
                    # Tracks with no name are unresolvable (e.g. unavailable in region).
                    if not track_data.get("name"):
                        skipped += 1
                        continue
                    tracks.append(_item_to_track(track_data))
                if not page.get("next"):
                    break
                offset += 100
        except spotipy.SpotifyException as exc:
            raise SpotifyServiceError(f"Failed to fetch playlist tracks: {exc}") from exc

        return tracks, skipped

    # ------------------------------------------------------------------
    # Audio features
    # ------------------------------------------------------------------

    def get_audio_features(self, spotify_ids: list[str]) -> dict[str, dict]:
        """Batch-fetch audio features for the given Spotify track IDs.

        Returns a mapping of ``spotify_id → feature dict`` containing at minimum
        ``energy``, ``danceability``, and ``valence`` (floats 0.0–1.0).
        IDs not found in the response are absent from the returned map.

        Raises :class:`SpotifyServiceError` on API failure.
        """
        if not spotify_ids:
            return {}
        sp = self._require_auth()
        try:
            raw = sp.audio_features(spotify_ids)
        except spotipy.SpotifyException as exc:
            raise SpotifyServiceError(f"Failed to fetch audio features: {exc}") from exc
        return {feat["id"]: feat for feat in (raw or []) if feat and feat.get("id")}

    def try_silent_authenticate(self) -> str | None:
        """Authenticate using a cached token without opening the browser.

        Returns the display name on success, or ``None`` if no valid cached
        token exists or auth fails.
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
                cache_path=str(self._CACHE_PATH),
                open_browser=False,
            )
            self._sp = spotipy.Spotify(auth_manager=auth_manager)
            user = self._sp.current_user()
            return user.get("display_name") or user.get("id") or "Unknown user"
        except Exception:
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
