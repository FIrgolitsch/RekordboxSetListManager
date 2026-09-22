"""Helper for applying a local Rekordbox match to a project Track."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rekordbox_set_list_manager.models.enums import MatchStatus
from rekordbox_set_list_manager.services.track_matcher import MatchStrategy  # noqa: TC001

if TYPE_CHECKING:
    from rekordbox_set_list_manager.models.track import Track


def apply_track_match(track: Track, local: Track | None) -> None:
    """Apply *local* Rekordbox metadata to *track*.

    If *local* is ``None`` the match is cleared (status → UNMATCHED, all
    Rekordbox and match-provenance fields set to ``None``).  Otherwise each
    non-``None`` field from *local* is copied and ``match_status`` is set to
    ``MANUALLY_MATCHED``.

    Parameters
    ----------
    track : Track
        The streaming track to update in-place.
    local : Track | None
        The local Rekordbox track to copy metadata from, or ``None`` to clear
        the existing match.

    """
    if local is None:
        track.filepath = None
        track.bpm = None
        track.key = None
        track.color = None
        track.rekordbox_id = None
        track.match_status = MatchStatus.UNMATCHED
        # None means "not yet matched" — a cleared match must not keep the
        # strategy/score of the binding it just dropped.
        track.match_strategy = None
        track.match_score = None
    else:
        if local.filepath:
            track.filepath = local.filepath
        if local.bpm is not None:
            track.bpm = local.bpm
        if local.key:
            track.key = local.key
        if local.color is not None:
            track.color = local.color
        if local.rekordbox_id is not None:
            track.rekordbox_id = local.rekordbox_id
        track.match_status = MatchStatus.MANUALLY_MATCHED


def apply_download_match(track: Track, local: Track, strategy: MatchStrategy) -> None:
    """Apply a Rekordbox collection match produced by download ingest.

    Unlike :func:`apply_track_match`, which stamps ``MANUALLY_MATCHED`` for a
    user-picked binding, this marks the automated re-match as ``MATCHED``
    with the deterministic strategy that produced it and a perfect score —
    the binding was derived from the downloaded file, not chosen by hand.

    Parameters
    ----------
    track : Track
        The project track to update in-place.
    local : Track
        The collection track to copy metadata from.
    strategy : MatchStrategy
        The deterministic strategy that produced the match.

    """
    if local.filepath:
        track.filepath = local.filepath
    if local.bpm is not None:
        track.bpm = local.bpm
    if local.key:
        track.key = local.key
    if local.color is not None:
        track.color = local.color
    if local.rekordbox_id is not None:
        track.rekordbox_id = local.rekordbox_id
    track.match_status = MatchStatus.MATCHED
    track.match_strategy = strategy.name.lower()
    track.match_score = 1.0
