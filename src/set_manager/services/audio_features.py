"""Spotify audio features fetch service with batch support."""

from __future__ import annotations

from set_manager.models.track import Track
from set_manager.services.spotify_service import SpotifyService, SpotifyServiceError

_BATCH_SIZE = 100


class AudioFeaturesError(Exception):
    """Raised when audio feature fetching fails."""


class AudioFeaturesService:
    """Fetches Spotify audio features and applies them to Track objects.

    Takes an already-authenticated :class:`SpotifyService` instance.
    All network calls happen inside :meth:`fetch_features`.
    """

    def __init__(self, spotify: SpotifyService) -> None:
        self._spotify = spotify

    def fetch_features(self, tracks: list[Track]) -> list[Track]:
        """Return *tracks* with energy/danceability/valence populated where possible.

        Only tracks that have a ``spotify_id`` are queried; tracks without one
        are returned as the same object (unchanged).  Tracks with a spotify_id
        get a ``model_copy(update=...)`` with the new audio feature fields set.

        Raises :class:`AudioFeaturesError` on API failure.
        """
        if not tracks:
            return []

        # Build an index of (original_list_index, track) for all spotify-linked tracks.
        indexed_spotify: list[tuple[int, Track]] = [
            (i, t) for i, t in enumerate(tracks) if t.spotify_id
        ]

        if not indexed_spotify:
            return list(tracks)

        result: list[Track] = list(tracks)  # shallow copy; replaced entries below

        for batch_start in range(0, len(indexed_spotify), _BATCH_SIZE):
            batch = indexed_spotify[batch_start : batch_start + _BATCH_SIZE]
            ids = [t.spotify_id for _, t in batch]  # all non-None by construction
            try:
                features_map = self._spotify.get_audio_features(ids)  # type: ignore[arg-type]
            except SpotifyServiceError as exc:
                raise AudioFeaturesError(f"Failed to fetch audio features: {exc}") from exc

            for original_idx, track in batch:
                feat = features_map.get(track.spotify_id)  # type: ignore[arg-type]
                if feat is None:
                    continue  # API returned no data for this track
                result[original_idx] = track.model_copy(
                    update={
                        "energy": feat.get("energy"),
                        "danceability": feat.get("danceability"),
                        "valence": feat.get("valence"),
                    }
                )

        return result
