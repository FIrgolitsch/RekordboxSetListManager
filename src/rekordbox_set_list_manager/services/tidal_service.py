"""Tidal integration service — authentication and playlist import."""

from __future__ import annotations

import concurrent.futures
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import platformdirs
import tidalapi

if TYPE_CHECKING:
    from collections.abc import Callable

    from tidalapi.user import LoggedInUser

from rekordbox_set_list_manager.models.enums import MatchStatus, TrackSource
from rekordbox_set_list_manager.models.track import Track
from rekordbox_set_list_manager.services import (
    _tidal_download_adapter as _tidal_adapter,
)
from rekordbox_set_list_manager.services.skipped_item import SkippedItem
from rekordbox_set_list_manager.services.streaming_base import PlaylistResult, StreamingService
from rekordbox_set_list_manager.utils import config

log = logging.getLogger(__name__)

_CACHE_DIR = Path(platformdirs.user_cache_dir("rekordbox_set_list_manager"))
_SESSION_FILE = _CACHE_DIR / "tidal_session.json"

# Config key recording which OAuth client minted the cached session file.
# tidalapi's session file cannot identify its own minting client, so the app
# stores a truncated sha256 of the client id at save time and refuses to load
# a session whose fingerprint does not match the resolved credentials.
_SESSION_CLIENT_KEY = "tidal_session_client"

# Poll interval while waiting on the device-code future so a cancel
# request is honoured within a fraction of a second.
_OAUTH_POLL_SECONDS = 0.2


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

    def authenticate(
        self,
        link_callback: Callable[[str], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> str:
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
        cancel_check : Callable[[], bool] | None
            If provided, polled while waiting for the user to approve the
            device-code login; returning ``True`` aborts the wait promptly
            instead of blocking until the link expires.

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
        # The client pair must sit on the session before any session-file
        # load or login — token refresh and device auth both post
        # config.client_id.  Credential application lives behind the download
        # adapter's seam so playlist import and downloads share one resolver;
        # a configured pair wins, otherwise the session keeps its embedded
        # defaults.
        fingerprint = _tidal_adapter.apply_client_credentials(session)

        # Fast path: load existing session from disk — but only when it was
        # minted under the current client pair.  An unfingerprinted file
        # (pre-dating the binding) counts as a mismatch and is re-authed.
        if _SESSION_FILE.exists():
            if _session_fingerprint_ok(fingerprint):
                _restrict_session_permissions()
            else:
                log.info(
                    "Cached Tidal session was minted under different client "
                    "credentials; re-authenticating"
                )
                _SESSION_FILE.unlink(missing_ok=True)
        try:
            loaded = session.load_session_from_file(_SESSION_FILE) and session.check_login()
        except Exception:
            log.warning("Cached Tidal session could not be restored", exc_info=True)
            loaded = False

        if not loaded:
            fn = link_callback if link_callback is not None else print
            try:
                _oauth_login(session, fn, cancel_check)
                session.save_session_to_file(_SESSION_FILE)
                _restrict_session_permissions()
                _record_session_client(fingerprint)
            except TidalServiceError:
                raise
            except Exception as exc:
                raise TidalServiceError(f"Authentication failed: {exc}") from exc

        # A restored session already passed check_login() above; only a
        # fresh device-code login still needs verification.
        if not loaded and not session.check_login():
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

    def get_playlist_tracks(self, playlist_id: str) -> PlaylistResult:
        """Fetch all tracks from *playlist_id* and return as Track objects.

        Parameters
        ----------
        playlist_id : str
            The Tidal playlist ID to fetch tracks from.

        Returns
        -------
        PlaylistResult
            ``tracks`` holds the converted tracks; ``skipped`` holds a
            :class:`SkippedItem` for each playlist entry that could not be
            converted.

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
            # num_tracks is unreliable (tidalapi defaults it to -1), so keep
            # fetching until the API returns an empty or short page.
            while True:
                batch = playlist.tracks(limit=limit, offset=offset)
                if not batch:
                    break
                raw_tracks.extend(batch)
                offset += len(batch)
                if len(batch) < limit:
                    break
        except Exception as exc:
            raise TidalServiceError(f"Failed to fetch playlist tracks: {exc}") from exc
        tracks, skipped = _convert_tracks(raw_tracks)
        return PlaylistResult(tracks=tracks, skipped=skipped)

    def try_silent_authenticate(self) -> str | None:
        """Load a cached Tidal session without initiating device-code flow.

        Returns
        -------
        str | None
            The username on success, or ``None`` if no valid cached session exists.

        """
        if not _SESSION_FILE.exists():
            return None
        _restrict_session_permissions()
        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            session = tidalapi.Session()
            fingerprint = _tidal_adapter.apply_client_credentials(session)
            if not _session_fingerprint_ok(fingerprint):
                log.info(
                    "Cached Tidal session was minted under different client "
                    "credentials; discarding it"
                )
                _SESSION_FILE.unlink(missing_ok=True)
                return None
            loaded = session.load_session_from_file(_SESSION_FILE) and session.check_login()
            if not loaded:
                return None
            self._session = session
            user = cast("LoggedInUser", session.user)
            return user.username or str(user.id)
        except Exception:
            # Silent path: any failure just means "no session to restore".
            log.debug("Silent Tidal session restore failed", exc_info=True)
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


def _oauth_login(
    session: tidalapi.Session,
    fn_print: Callable[[str], None],
    cancel_check: Callable[[], bool] | None,
) -> None:
    """Run the device-code login, honouring a cooperative cancel request.

    Falls back to tidalapi's blocking ``login_oauth_simple`` when no
    *cancel_check* is given; otherwise polls the login future so a cancel
    is honoured within ``_OAUTH_POLL_SECONDS`` instead of blocking until
    the link expires (~5 minutes).

    Raises
    ------
    TidalServiceError
        If *cancel_check* aborts the flow.
    """
    if cancel_check is None:
        session.login_oauth_simple(fn_print=fn_print)
        return
    login, future = session.login_oauth()
    fn_print(
        f"Visit https://{login.verification_uri_complete} to log in, "
        f"the code will expire in {login.expires_in} seconds"
    )
    while True:
        if cancel_check():
            future.cancel()
            raise TidalServiceError("Authentication cancelled.")
        try:
            future.result(timeout=_OAUTH_POLL_SECONDS)
        except concurrent.futures.TimeoutError:
            continue
        else:
            return


def _restrict_session_permissions() -> None:
    """Restrict the OAuth session file to owner-only (0600)."""
    try:
        _SESSION_FILE.chmod(0o600)
    except OSError:
        log.debug("Could not restrict Tidal session file permissions", exc_info=True)


def _session_fingerprint_ok(fingerprint: str) -> bool:
    """Return ``True`` when the cached session's minting client matches.

    Compares the fingerprint recorded under ``tidal_session_client`` at
    session-save time with *fingerprint* of the currently resolved client
    pair.  A file with no recorded fingerprint predates the binding and
    counts as a mismatch — a session is never trusted under an unknown
    minting client.

    Parameters
    ----------
    fingerprint : str
        The :func:`client_fingerprint` of the resolved client pair.

    Returns
    -------
    bool
        ``True`` when the stored fingerprint matches the current client.

    """
    return config.get(_SESSION_CLIENT_KEY) == fingerprint


def _record_session_client(fingerprint: str) -> None:
    """Record which client minted the session file (best-effort).

    A write failure must not sink a successful authentication, so errors are
    logged at debug level and swallowed.

    Parameters
    ----------
    fingerprint : str
        The :func:`client_fingerprint` of the client pair that minted the
        freshly saved session.

    """
    try:
        config.set_value(_SESSION_CLIENT_KEY, fingerprint)
    except OSError:
        log.debug("Could not persist the Tidal session client fingerprint", exc_info=True)


def _convert_tracks(raw_tracks: list) -> tuple[list[Track], list[SkippedItem]]:
    """Convert raw tidalapi tracks, collecting a SkippedItem per unreadable entry."""
    tracks: list[Track] = []
    skipped: list[SkippedItem] = []
    for index, raw in enumerate(raw_tracks):
        try:
            tracks.append(_tidal_to_track(raw))
        except Exception:  # a malformed item must not sink the whole import
            raw_id = getattr(raw, "id", None)
            label = str(raw_id) if raw_id is not None else f"Track at position {index + 1}"
            skipped.append(SkippedItem(label=label, reason="could not be converted"))
            log.debug("Skipping unreadable Tidal playlist item", exc_info=True)
    return tracks, skipped


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
