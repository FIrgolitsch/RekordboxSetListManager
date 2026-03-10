"""Tidal integration service — authentication and playlist import."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import platformdirs
import tidalapi

from set_manager.models.enums import MatchStatus, TrackSource
from set_manager.models.track import Track

_CACHE_DIR = Path(platformdirs.user_cache_dir("set_manager"))
_SESSION_FILE = _CACHE_DIR / "tidal_session.json"


class TidalServiceError(Exception):
    """Raised when a Tidal API operation fails."""


class TidalService:
    """Wraps tidalapi for playlist import.

    Call :meth:`authenticate` once before any other method.
    The session is cached to disk via tidalapi's session file and reused across
    restarts — so the device-code browser flow is only needed on first run.
    """

    def __init__(self) -> None:
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
        Returns the authenticated user's display name (username).

        Raises:
            TidalServiceError: If auth fails or is not completed.
        """
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        session = tidalapi.Session()

        # Fast path: load existing session from disk.
        try:
            loaded = session.load_session_from_file(_SESSION_FILE) and session.check_login()
        except Exception:
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
        return session.user.username or str(session.user.id)

    # ------------------------------------------------------------------
    # Playlists
    # ------------------------------------------------------------------

    def get_playlists(self) -> list[dict]:
        """Return metadata for all the user's playlists (owned + saved).

        Returns a list of ``{"id": str, "name": str, "track_count": int}``.

        Raises:
            TidalServiceError: If not authenticated or the API call fails.
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

    def get_playlist_tracks(self, playlist_id: str) -> list[Track]:
        """Fetch all tracks from *playlist_id* and return as Track objects.

        Each track has ``source=TIDAL``, ``tidal_id``, ``isrc`` (if available),
        and ``match_status=UNMATCHED``.

        Raises:
            TidalServiceError: If not authenticated or the API call fails.
        """
        session = self._require_auth()
        try:
            playlist = session.playlist(playlist_id)
            raw_tracks = playlist.tracks()
        except Exception as exc:
            raise TidalServiceError(f"Failed to fetch playlist tracks: {exc}") from exc
        return [_tidal_to_track(t) for t in raw_tracks]

    def try_silent_authenticate(self) -> str | None:
        """Load a cached Tidal session without initiating device-code flow.

        Returns the username on success, or ``None`` if no valid cached session
        exists.
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
            return session.user.username or str(session.user.id)
        except Exception:
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
    try:
        return session.user.playlist_and_favorite_playlists()
    except Exception:
        pass  # fall through to the safer two-call approach

    seen: set[str] = set()
    result = []
    for pl in session.user.playlists():
        key = str(pl.id)
        if key not in seen:
            seen.add(key)
            result.append(pl)
    try:
        for pl in session.user.favorites.playlists():
            key = str(pl.id)
            if key not in seen:
                seen.add(key)
                result.append(pl)
    except Exception:
        pass  # favourites unavailable — owned playlists are still returned
    return result


def _tidal_to_track(t) -> Track:
    """Convert a tidalapi Track to a set_manager Track."""
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
