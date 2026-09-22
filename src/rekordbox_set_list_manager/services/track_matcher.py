"""Multi-strategy track matcher: links Spotify tracks to local Rekordbox files."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING

from rapidfuzz import fuzz, process, utils

from rekordbox_set_list_manager.models.enums import MatchStatus
from rekordbox_set_list_manager.services.skipped_item import SkippedItem

if TYPE_CHECKING:
    from rekordbox_set_list_manager.models.track import Track


class MatchStrategy(Enum):
    """Strategy used when matching a streaming track to a local collection entry."""

    ISRC = auto()
    EXACT = auto()
    FILEPATH = auto()
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


@dataclass
class _CollectionIndex:
    """Lookup structures built once per :meth:`TrackMatcher.match` call.

    ``isrc`` maps an ISRC to every collection track carrying it so duplicate
    groups can be resolved deterministically and reported. ``exact`` is the
    normalized ``(title, artist)`` lookup. ``buckets`` groups tracks by the
    first character of their processed title and of their processed artist so
    the fuzzy stage only scores a small candidate set instead of the whole
    collection.
    """

    isrc: dict[str, list[Track]]
    exact: dict[tuple[str, str], list[Track]]
    buckets: dict[str, list[Track]]


class TrackMatcher:
    """Match a list of Spotify tracks against a local (Rekordbox) collection.

    Strategies are tried in priority order:
    1. ISRC exact match
    2. Artist + title exact match (case-insensitive)
    3. Fuzzy title+artist match (rapidfuzz token_sort_ratio ≥ threshold)
    4. Filename-based match (parse stem as "Artist - Title")

    Attributes
    ----------
    isrc_duplicates : list[SkippedItem]
        Duplicate-ISRC groups discovered while indexing the collection during
        the most recent :meth:`match` call. The list is reset on every call; a
        track with a duplicated ISRC still matches (deterministically, to the
        lowest Rekordbox ID), and the group is reported here so the ambiguity
        can be surfaced to the user.
    """

    FUZZY_THRESHOLD = 85  # rapidfuzz score out of 100

    def __init__(self) -> None:
        self.isrc_duplicates: list[SkippedItem] = []

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
        self.isrc_duplicates = []
        index = self._build_index(collection)
        return [self._match_one(sp_track, collection, index) for sp_track in spotify_tracks]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _build_index(self, collection: list[Track]) -> _CollectionIndex:
        isrc_index: dict[str, list[Track]] = {}
        exact_index: dict[tuple[str, str], list[Track]] = {}
        buckets: dict[str, list[Track]] = {}

        for local in collection:
            if local.isrc:
                isrc_index.setdefault(local.isrc, []).append(local)
            # Text indexes skip entries without both fields; they remain
            # reachable via ISRC and the filename fallback.
            if not local.title or not local.artist:
                continue
            exact_index.setdefault((_normalise(local.title), _normalise(local.artist)), []).append(
                local
            )
            title_key = utils.default_process(local.title) or ""
            artist_key = utils.default_process(local.artist) or ""
            if title_key:
                buckets.setdefault(title_key[0], []).append(local)
            if artist_key and (not title_key or artist_key[0] != title_key[0]):
                buckets.setdefault(artist_key[0], []).append(local)

        # A shared ISRC is ambiguous: the lowest Rekordbox ID wins
        # deterministically and the group is reported so the user sees it.
        for isrc, tracks in isrc_index.items():
            if len(tracks) > 1:
                self.isrc_duplicates.append(
                    SkippedItem(
                        label=isrc,
                        reason=(f"{len(tracks)} tracks share this ISRC; lowest Rekordbox ID used"),
                    )
                )

        return _CollectionIndex(isrc=isrc_index, exact=exact_index, buckets=buckets)

    def _match_one(
        self,
        sp_track: Track,
        collection: list[Track],
        index: _CollectionIndex,
    ) -> MatchResult:
        # 1. ISRC exact — duplicate ISRCs resolve to the lowest Rekordbox ID.
        #    Runs before the title/artist bail-out: the comparison needs
        #    neither text field, so a track with a valid ISRC but an empty
        #    artist (e.g. an empty Spotify artists list) can still match.
        if sp_track.isrc and sp_track.isrc in index.isrc:
            local = min(index.isrc[sp_track.isrc], key=_id_key)
            return MatchResult(
                spotify_track=sp_track,
                local_track=_merge(sp_track, local, MatchStrategy.ISRC, 1.0),
                strategy=MatchStrategy.ISRC,
                score=1.0,
            )

        # The text strategies need both a title and an artist.
        if not sp_track.title or not sp_track.artist:
            return MatchResult(
                spotify_track=sp_track,
                local_track=None,
                strategy=MatchStrategy.NONE,
                score=0.0,
            )

        # 2. Exact title + artist on the normalized index
        sp_key = (_normalise(sp_track.title), _normalise(sp_track.artist))
        if sp_key in index.exact:
            local = min(index.exact[sp_key], key=_id_key)
            return MatchResult(
                spotify_track=sp_track,
                local_track=_merge(sp_track, local, MatchStrategy.EXACT, 1.0),
                strategy=MatchStrategy.EXACT,
                score=1.0,
            )

        # 3. Fuzzy title + artist over the pre-filtered bucket
        sp_str = f"{sp_track.title} {sp_track.artist}"
        result = self._fuzzy_match(sp_track, sp_str, index)
        if result is not None:
            return result

        # 4. Filename-based match
        return self._filename_match(sp_track, sp_str, collection)

    def _fuzzy_match(
        self,
        sp_track: Track,
        sp_str: str,
        index: _CollectionIndex,
    ) -> MatchResult | None:
        candidates = _bucket_candidates(sp_track, index.buckets)
        if not candidates:
            return None

        # extract() keys results by the choice string — dedupe identical
        # "{title} {artist}" choices and keep every track behind each one.
        choice_map: dict[str, list[Track]] = {}
        for local in candidates:
            choice_map.setdefault(f"{local.title} {local.artist}", []).append(local)

        scored = process.extract(
            sp_str,
            list(choice_map),
            scorer=fuzz.token_sort_ratio,
            processor=utils.default_process,
            score_cutoff=self.FUZZY_THRESHOLD,
        )
        if not scored:
            return None

        # Ties resolve to the lowest Rekordbox ID so results do not depend on
        # collection ordering.
        best_score = scored[0][1]
        tied = [
            track
            for choice, score, _idx in scored
            if score == best_score
            for track in choice_map[choice]
        ]
        best_local = min(tied, key=_id_key)
        return MatchResult(
            spotify_track=sp_track,
            local_track=_merge(sp_track, best_local, MatchStrategy.FUZZY, best_score / 100),
            strategy=MatchStrategy.FUZZY,
            score=best_score / 100,
        )

    def _filename_match(
        self,
        sp_track: Track,
        sp_str: str,
        collection: list[Track],
    ) -> MatchResult:
        best_score = 0.0
        best_local: Track | None = None
        for local in collection:
            if not local.filepath:
                continue
            stem = Path(local.filepath).stem
            # Try "Artist - Title" and "Title - Artist" formats
            for candidate in _parse_filename_candidates(stem):
                score = fuzz.token_sort_ratio(sp_str, candidate, processor=utils.default_process)
                if score < self.FUZZY_THRESHOLD:
                    continue
                if (
                    best_local is None
                    or score > best_score
                    or (score == best_score and _id_key(local) < _id_key(best_local))
                ):
                    best_score = score
                    best_local = local

        if best_local is not None:
            return MatchResult(
                spotify_track=sp_track,
                local_track=_merge(sp_track, best_local, MatchStrategy.FILENAME, best_score / 100),
                strategy=MatchStrategy.FILENAME,
                score=best_score / 100,
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


def _id_key(track: Track) -> tuple[bool, int, str]:
    """Sort key for deterministic lowest-Rekordbox-ID resolution.

    Tracks without a Rekordbox ID sort after every IDed track; the filepath
    breaks any remaining tie so ordering never depends on collection order.
    """
    rid = track.rekordbox_id
    return (rid is None, rid if rid is not None else 0, track.filepath or "")


def _bucket_candidates(sp_track: Track, buckets: dict[str, list[Track]]) -> list[Track]:
    """Return collection tracks sharing a processed title/artist initial.

    A track joins up to two buckets (first character of its processed title and
    of its processed artist), so a query whose title OR artist shares an
    initial with a candidate still finds it.
    """
    keys = set()
    title_key = utils.default_process(sp_track.title) or ""
    artist_key = utils.default_process(sp_track.artist) or ""
    if title_key:
        keys.add(title_key[0])
    if artist_key:
        keys.add(artist_key[0])

    seen: set[int] = set()
    candidates: list[Track] = []
    for key in keys:
        for track in buckets.get(key, ()):
            if id(track) not in seen:
                seen.add(id(track))
                candidates.append(track)
    return candidates


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
