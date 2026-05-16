"""Multi-strategy track matcher: links Spotify tracks to local Rekordbox files."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING

from thefuzz import fuzz

from rekordbox_set_list_manager.models.enums import MatchStatus

if TYPE_CHECKING:
    from rekordbox_set_list_manager.models.track import Track


class MatchStrategy(Enum):
    """Strategy used when matching a streaming track to a local collection entry."""

    ISRC = auto()
    EXACT = auto()
    FUZZY = auto()
    FILENAME = auto()
    NONE = auto()


@dataclass
class MatchResult:
    """Result of matching one Spotify track against a local collection."""

    spotify_track: Track
    local_track: Track | None
    strategy: MatchStrategy
    score: float  # 0.0-1.0; 1.0 for exact/ISRC, fractional for fuzzy


class TrackMatcher:
    """Match a list of Spotify tracks against a local (Rekordbox) collection.

    Strategies are tried in priority order:
    1. ISRC exact match
    2. Artist + title exact match (case-insensitive)
    3. Fuzzy title+artist match (thefuzz token_sort_ratio ≥ threshold)
    4. Filename-based match (parse stem as "Artist - Title")
    """

    FUZZY_THRESHOLD = 85  # thefuzz score out of 100

    def match(
        self,
        spotify_tracks: list[Track],
        collection: list[Track],
    ) -> list[MatchResult]:
        """Return one :class:`MatchResult` per spotify track.

        Parameters
        ----------
        spotify_tracks : list[Track]
            Tracks imported from Spotify (or another streaming service) to match.
        collection : list[Track]
            The local Rekordbox collection to match against.

        Returns
        -------
        list[MatchResult]
            One result per input track, in the same order as *spotify_tracks*.

        """
        # Pre-build lookup structures for O(1) / O(n) matching
        isrc_index: dict[str, Track] = {}
        for local in collection:
            if local.isrc:
                isrc_index.setdefault(local.isrc, local)

        results: list[MatchResult] = []
        for sp_track in spotify_tracks:
            result = self._match_one(sp_track, collection, isrc_index)
            results.append(result)
        return results

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _match_one(
        self,
        sp_track: Track,
        collection: list[Track],
        isrc_index: dict[str, Track],
    ) -> MatchResult:
        # 1. ISRC exact
        if sp_track.isrc and sp_track.isrc in isrc_index:
            local = isrc_index[sp_track.isrc]
            return MatchResult(
                spotify_track=sp_track,
                local_track=_merge(sp_track, local, MatchStrategy.ISRC, 1.0),
                strategy=MatchStrategy.ISRC,
                score=1.0,
            )

        # 2. Exact title + artist (case-fold)
        sp_key = (_normalise(sp_track.title), _normalise(sp_track.artist))
        for local in collection:
            if (_normalise(local.title), _normalise(local.artist)) == sp_key:
                return MatchResult(
                    spotify_track=sp_track,
                    local_track=_merge(sp_track, local, MatchStrategy.EXACT, 1.0),
                    strategy=MatchStrategy.EXACT,
                    score=1.0,
                )

        # 3. Fuzzy title + artist
        sp_str = f"{sp_track.title} {sp_track.artist}"
        best_score = 0
        best_local: Track | None = None
        for local in collection:
            local_str = f"{local.title} {local.artist}"
            score = fuzz.token_sort_ratio(sp_str, local_str)
            if score > best_score:
                best_score = score
                best_local = local
        if best_local is not None and best_score >= self.FUZZY_THRESHOLD:
            return MatchResult(
                spotify_track=sp_track,
                local_track=_merge(sp_track, best_local, MatchStrategy.FUZZY, best_score / 100),
                strategy=MatchStrategy.FUZZY,
                score=best_score / 100,
            )

        # 4. Filename-based match
        for local in collection:
            if not local.filepath:
                continue
            stem = Path(local.filepath).stem
            # Try "Artist - Title" and "Title - Artist" formats
            for candidate in _parse_filename_candidates(stem):
                score = fuzz.token_sort_ratio(sp_str, candidate)
                if score >= self.FUZZY_THRESHOLD:
                    return MatchResult(
                        spotify_track=sp_track,
                        local_track=_merge(sp_track, local, MatchStrategy.FILENAME, score / 100),
                        strategy=MatchStrategy.FILENAME,
                        score=score / 100,
                    )

        return MatchResult(
            spotify_track=sp_track,
            local_track=None,
            strategy=MatchStrategy.NONE,
            score=0.0,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalise(s: str) -> str:
    return s.casefold().strip()


def _parse_filename_candidates(stem: str) -> list[str]:
    """Return possible 'Title Artist' strings derived from a filename stem."""
    parts = [p.strip() for p in stem.split(" - ", maxsplit=1)]
    if len(parts) == 2:  # noqa: PLR2004
        # "Artist - Title" → "Title Artist" and "Artist Title"
        return [f"{parts[1]} {parts[0]}", f"{parts[0]} {parts[1]}"]
    return [stem]


def _merge(
    spotify_track: Track, local_track: Track, strategy: MatchStrategy, score: float
) -> Track:
    """Return a new Track combining Spotify metadata with local file data."""
    return spotify_track.model_copy(
        update={
            "filepath": local_track.filepath,
            "bpm": local_track.bpm,
            "key": local_track.key,
            "color": local_track.color,
            "rekordbox_id": local_track.rekordbox_id,
            "match_status": MatchStatus.MATCHED,
            "match_strategy": strategy.name.lower(),
            "match_score": score,
        }
    )
