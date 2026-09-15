"""Shared detail type for items skipped during import or export."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SkippedItem:
    """A track or playlist entry that was skipped, with the reason why.

    Shared by the streaming services and the Rekordbox DB exporter so every
    skipped item carries enough detail to be surfaced to the user instead of
    being reduced to a bare count.

    Attributes
    ----------
    label : str
        Human-readable identifier for the skipped item (track name,
        playlist position, or a placeholder when nothing is known).
    reason : str
        Why the item was skipped (e.g. "no track name", "not found in
        Rekordbox collection").
    """

    label: str
    reason: str
