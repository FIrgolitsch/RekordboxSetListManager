"""Helper for applying a local Rekordbox match to a project Track."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rekordbox_set_list_manager.models.enums import MatchStatus

if TYPE_CHECKING:
    from rekordbox_set_list_manager.models.track import Track


def apply_track_match(track: Track, local: Track | None) -> None:
    """Apply *local* Rekordbox metadata to *track*.

    If *local* is ``None`` the match is cleared (status → UNMATCHED, all
    Rekordbox fields set to ``None``).  Otherwise each non-``None`` field from
    *local* is copied and ``match_status`` is set to ``MANUALLY_MATCHED``.

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
