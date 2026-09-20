"""Abstract base class for streaming music service integrations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from rekordbox_set_list_manager.models.track import Track
    from rekordbox_set_list_manager.services.skipped_item import SkippedItem


@dataclass
class PlaylistResult:
    """Outcome of fetching a playlist's tracks from a streaming service.

    Attributes
    ----------
    tracks : list[Track]
        Playlist entries that could be imported as :class:`Track` objects.
    skipped : list[SkippedItem]
        Entries that were dropped (null slots, episodes, nameless or
        unreadable items), each carrying a label and the reason why.

    """

    tracks: list[Track] = field(default_factory=list)
    skipped: list[SkippedItem] = field(default_factory=list)


class StreamingService(ABC):
    """Common interface for Spotify and Tidal (and future) streaming services.

    Concrete subclasses must implement all abstract methods.  Each method that
    requires authentication should raise the service-specific ``*ServiceError``
    when not yet authenticated.
    """

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    @abstractmethod
    def try_silent_authenticate(self) -> str | None:
        """Try to authenticate using cached credentials without user interaction.

        Returns
        -------
        str | None
            The display name on success, or ``None`` if no cached session is
            available or the cached token has expired.

        """

    @abstractmethod
    def authenticate(
        self,
        link_callback: Callable[[str], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
        redirect_callback: Callable[[], str] | None = None,
    ) -> str:
        """Authenticate and return the connected user's display name.

        Parameters
        ----------
        link_callback : Callable[[str], None] | None
            Passed the login URL for link-based flows (Tidal).  Ignored for
            browser-redirect flows (Spotify).
        cancel_check : Callable[[], bool] | None
            Optional callable polled while waiting for the user to complete
            the login.  When it returns ``True`` the flow aborts with a
            service error instead of blocking until the link expires.
            Services whose flow cannot be interrupted may ignore it.
        redirect_callback : Callable[[], str] | None
            Optional callable that returns the browser redirect URL the user
            pastes back, for PKCE flows that cannot be intercepted (Tidal).
            ``None`` selects the service's non-interactive fallback flow.
            Ignored by services that do not need it.

        Returns
        -------
        str
            The authenticated user's display name.

        """

    @abstractmethod
    def is_authenticated(self) -> bool:
        """Return ``True`` if an active session exists.

        Returns
        -------
        bool
            ``True`` when the service has an active authenticated session.

        """

    # ------------------------------------------------------------------
    # Playlists / tracks
    # ------------------------------------------------------------------

    @abstractmethod
    def get_playlists(self) -> list[dict]:
        """Return the authenticated user's playlists.

        Returns
        -------
        list[dict]
            Each item is a ``dict`` with at minimum ``"id"`` and ``"name"`` keys.

        """

    @abstractmethod
    def get_playlist_tracks(self, playlist_id: str) -> PlaylistResult:
        """Fetch all tracks from *playlist_id*.

        Parameters
        ----------
        playlist_id : str
            The service-specific playlist identifier.

        Returns
        -------
        PlaylistResult
            The importable tracks plus one :class:`SkippedItem` per playlist
            entry that could not be imported.  For services that do not track
            skipped items, ``skipped`` is empty.

        """
