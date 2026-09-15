"""Adapter isolating private spotipy API usage and cache-file hardening.

The private ``spotipy.Spotify._get`` playlist-items call lives behind this
single seam so a spotipy API change produces one localized failure.
``SecureCacheFileHandler`` additionally locks the OAuth token cache file
down to owner-only permissions after writes and on reads of pre-existing
files.
"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from spotipy.cache_handler import CacheFileHandler

from rekordbox_set_list_manager.services.skipped_item import SkippedItem

if TYPE_CHECKING:
    import spotipy

log = logging.getLogger(__name__)


def fetch_playlist_items(
    sp: spotipy.Spotify,
    playlist_id: str,
) -> tuple[list[dict], list[SkippedItem]]:
    """Fetch all track items from a Spotify playlist.

    Parameters
    ----------
    sp : spotipy.Spotify
        Authenticated spotipy client.
    playlist_id : str
        The Spotify playlist ID to fetch items from.

    Returns
    -------
    tuple[list[dict], list[SkippedItem]]
        ``(track_dicts, skipped)`` where *track_dicts* are the raw Spotify
        track objects for importable items and *skipped* holds one
        :class:`SkippedItem` per dropped entry (null slots, episodes, and
        nameless tracks).

    """
    # Use sp._get() directly to avoid spotipy's additional_types default
    # ("track,episode"), which causes the Spotify API to nullify track fields
    # for some playlists. Without additional_types only track items are returned.
    track_dicts: list[dict] = []
    skipped: list[SkippedItem] = []
    offset = 0
    while True:
        page = sp._get(  # type: ignore[attr-defined]
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
                label = track_data.get("name") if track_data else "(empty playlist slot)"
                skipped.append(
                    SkippedItem(
                        label=label or "(unknown item)",
                        reason="not a track (episode or empty slot)",
                    )
                )
                continue
            # Tracks with no name are unresolvable (e.g. unavailable in region).
            if not track_data.get("name"):
                skipped.append(
                    SkippedItem(
                        label=track_data.get("id") or "(unknown item)",
                        reason="no track name (unavailable)",
                    )
                )
                continue
            track_dicts.append(track_data)
        if not page.get("next"):
            break
        offset += 100
    return track_dicts, skipped


class SecureCacheFileHandler(CacheFileHandler):
    """Cache handler that keeps the OAuth token file owner-only.

    spotipy may leave ``.spotify_cache`` with default (world-readable on
    some umasks) permissions; this handler re-applies ``0o600`` after every
    save and opportunistically when reading a pre-existing cache file.
    """

    def save_token_to_cache(self, token_info: dict[str, Any]) -> None:
        """Save *token_info* then restrict the cache file to owner-only.

        Parameters
        ----------
        token_info : dict[str, Any]
            The spotipy token payload to persist.

        """
        super().save_token_to_cache(token_info)
        self._restrict_permissions()

    def get_cached_token(self) -> dict[str, Any] | None:
        """Return the cached token, restricting a pre-existing file first.

        Returns
        -------
        dict[str, Any] | None
            The cached token payload, or ``None`` when no valid cache exists.

        """
        if Path(self.cache_path).exists():
            self._restrict_permissions()
        return super().get_cached_token()

    def _restrict_permissions(self) -> None:
        try:
            Path(self.cache_path).chmod(0o600)
        except OSError:
            log.debug("Could not restrict Spotify token cache permissions", exc_info=True)
