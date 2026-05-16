"""Tidal integration service — authentication and playlist import."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import platformdirs
import tidalapi

if TYPE_CHECKING:
    from collections.abc import Callable

    from tidalapi.user import LoggedInUser

from rekordbox_set_list_manager.models.enums import MatchStatus, TrackSource
from rekordbox_set_list_manager.models.track import Track
from rekordbox_set_list_manager.services.streaming_base import StreamingService

_CACHE_DIR = Path(platformdirs.user_cache_dir("rekordbox_set_list_manager"))
_SESSION_FILE = _CACHE_DIR / "tidal_session.json"


class TidalServiceError(Exception):
    """Raised when a Tidal API operation fails."""


class TidalService(StreamingService):
    """Wraps tidalapi for playlist import.

    Call :meth:`authenticate` once before any other method.
    The session is cached to disk via tidalapi's session file and reused across
    restarts — so the device-code browser flow is only needed on first run.
    """

    def __init__(self) -> None:
        """Initialise the Tidal service with no active session."""
        self._session: tidalapi.Session | None = None

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def authenticate(self, link_callback: Callable[[str], None] | None = None) -> str:
        """Authenticate with Tidal using the device code flow.

        On first run initiates the device-code flow.  If *link_callback* is
        provided it is called with the login URL message so the caller can
        display it in the UI; otherwise the message is printed to stdout.
        Subsequent calls reuse the cached session (or refresh the token).

        Parameters
        ----------
        link_callback : Callable[[str], None] | None
            If provided, called with the login URL for device-code flow display.
            Defaults to :func:`print` if ``None``.

        Returns
        -------
        str
            The authenticated user's display name (username).

        Raises
        ------
        TidalServiceError
            If auth fails or is not completed.

        """
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        session = tidalapi.Session()

        # Fast path: load existing session from disk.
        try:
            loaded = session.load_session_from_file(_SESSION_FILE) and session.check_login()
        except Exception:  # noqa: BLE001
            loaded = False

        if not loaded:
            fn = link_callback if link_callback is not None else print
            try:
                session.login_oauth_simple(fn_print=fn)
                session.save_session_to_file(_SESSION_FILE)
            except Exception as exc:
                raise TidalServiceError(f"Authentication failed: {exc}") from exc

        if not session.check_login():
            raise TidalServiceError(
                "Tidal authentication was not completed. "
                "Please visit the link shown and approve access."
            )

        self._session = session
        user = cast("LoggedInUser", session.user)
        return user.username or str(user.id)

    def is_authenticated(self) -> bool:
        """Return ``True`` if a Tidal session is active.

        Returns
        -------
        bool
            ``True`` when an authenticated :class:`tidalapi.Session` exists.

        """
        return self._session is not None

    # ------------------------------------------------------------------
    # Playlists
    # ------------------------------------------------------------------

    def get_playlists(self) -> list[dict]:
        """Return metadata for all the user's playlists (owned + saved).

        Returns
        -------
        list[dict]
            Each entry contains ``"id"`` (str), ``"name"`` (str), and
            ``"track_count"`` (int) keys.

        Raises
        ------
        TidalServiceError
            If not authenticated or the API call fails.

        """
        session = self._require_auth()
        try:
            raw = _fetch_all_playlists(session)
        except Exception as exc:
            raise TidalServiceError(f"Failed to fetch playlists: {exc}") from exc
        return [
            {
                "id": str(pl.id),
                "name": pl.name,
                "track_count": pl.num_tracks,
            }
            for pl in raw
        ]

    # ------------------------------------------------------------------
    # Tracks
    # ------------------------------------------------------------------

    def get_playlist_tracks(self, playlist_id: str) -> tuple[list[Track], int]:
        """Fetch all tracks from *playlist_id* and return as Track objects.

        Parameters
        ----------
        playlist_id : str
            The Tidal playlist ID to fetch tracks from.

        Returns
        -------
        tuple[list[Track], int]
            ``(tracks, 0)`` — Tidal does not skip items the way Spotify does,
            so the skipped count is always ``0``.

        Raises
        ------
        TidalServiceError
            If not authenticated or the API call fails.

        """
        session = self._require_auth()
        try:
            playlist = session.playlist(playlist_id)
            raw_tracks: list = []
            limit = 100
            offset = 0
            total = playlist.num_tracks
            while offset < total:
                batch = playlist.tracks(limit=limit, offset=offset)
                if not batch:
                    break
                raw_tracks.extend(batch)
                offset += len(batch)
                if len(batch) < limit:
                    break
        except Exception as exc:
            raise TidalServiceError(f"Failed to fetch playlist tracks: {exc}") from exc
        return [_tidal_to_track(t) for t in raw_tracks], 0

    def try_silent_authenticate(self) -> str | None:
        """Load a cached Tidal session without initiating device-code flow.

        Returns
        -------
        str | None
            The username on success, or ``None`` if no valid cached session exists.

        """
        if not _SESSION_FILE.exists():
            return None
        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            session = tidalapi.Session()
            loaded = session.load_session_from_file(_SESSION_FILE) and session.check_login()
            if not loaded:
                return None
            self._session = session
            user = cast("LoggedInUser", session.user)
            return user.username or str(user.id)
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _require_auth(self) -> tidalapi.Session:
        if self._session is None:
            raise TidalServiceError("Not authenticated. Call authenticate() first.")
        return self._session


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _fetch_all_playlists(session: tidalapi.Session) -> list:
    """Return owned + favourite playlists without using the unreliable combined endpoint.

    Tries ``playlist_and_favorite_playlists()`` first; if the server returns an
    error (e.g. 500) falls back to fetching owned playlists and favourite
    playlists separately and deduplicating by ID.
    """
    user = cast("LoggedInUser", session.user)
    try:
        return user.playlist_and_favorite_playlists()
    except Exception:  # noqa: BLE001, S110
        pass  # fall through to the safer two-call approach

    seen: set[str] = set()
    result = []
    for pl in user.playlists():
        key = str(pl.id)
        if key not in seen:
            seen.add(key)
            result.append(pl)
    try:
        for pl in user.favorites.playlists():
            key = str(pl.id)
            if key not in seen:
                seen.add(key)
                result.append(pl)
    except Exception:  # noqa: BLE001, S110
        pass  # favourites unavailable — owned playlists are still returned
    return result


def _tidal_to_track(t: Any) -> Track:  # noqa: ANN401
    """Convert a tidalapi Track to a rekordbox_set_list_manager Track."""
    artist = t.artist.name if t.artist else ""
    bpm = t.bpm if (hasattr(t, "bpm") and t.bpm) else None
    return Track(
        title=t.title,
        artist=artist,
        duration=t.duration,  # tidalapi returns seconds directly
        isrc=t.isrc or None,
        bpm=float(bpm) if bpm else None,
        tidal_id=str(t.id),
        source=TrackSource.TIDAL,
        match_status=MatchStatus.UNMATCHED,
    )
