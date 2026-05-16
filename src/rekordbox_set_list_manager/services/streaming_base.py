"""Abstract base class for streaming music service integrations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from rekordbox_set_list_manager.models.track import Track


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
    ) -> str:
        """Authenticate and return the connected user's display name.

        Parameters
        ----------
        link_callback : Callable[[str], None] | None
            Passed the login URL for device-code flows (Tidal).  Ignored for
            browser-redirect flows (Spotify).

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
    def get_playlist_tracks(self, playlist_id: str) -> tuple[list[Track], int]:
        """Fetch all tracks from *playlist_id*.

        Parameters
        ----------
        playlist_id : str
            The service-specific playlist identifier.

        Returns
        -------
        tuple[list[Track], int]
            ``(tracks, skipped)`` where *skipped* is the count of items that
            could not be imported.  For services that do not track skipped items,
            *skipped* is ``0``.

        """
