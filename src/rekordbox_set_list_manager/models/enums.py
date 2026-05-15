"""Shared enumerations for the Set Manager domain."""

from enum import IntEnum, StrEnum


class SectionType(StrEnum):
    """Semantic role of a section within a DJ set."""

    OPENER = "opener"
    WARM_UP = "warm_up"
    BUILD = "build"
    PEAK = "peak"
    AFTER_PEAK = "after_peak"
    CLOSING = "closing"
    BREAK = "break"
    GENERAL = "general"


class RekordboxColor(IntEnum):
    """Track color codes used in Rekordbox XML (24-bit RGB integers).

    Values are hardcoded in the Rekordbox binary — not user-configurable.
    DjmdColor.ColorCode is always NULL in Rekordbox 7; the XML ``Colour``
    attribute (decimal integer) is the authoritative source.
    Verified against Rekordbox 7.2.14 exports (Phase I).
    See ``scripts/verify_rb_colors.py`` to re-verify against a live export.
    """

    NONE = 0
    PINK = 0xF870F8
    RED = 0xF87070
    ORANGE = 0xFFA064
    YELLOW = 0xF8E550
    GREEN = 0x1EE12B
    AQUA = 0x10E4DC
    BLUE = 0x1E50F0
    PURPLE = 0x9828F0


class MatchStatus(StrEnum):
    """How a track was matched between streaming metadata and local files."""

    UNMATCHED = "unmatched"
    MATCHED = "matched"
    MANUALLY_MATCHED = "manually_matched"
    CONFLICTED = "conflicted"


class TrackSource(StrEnum):
    """Where the track metadata originated."""

    SPOTIFY = "spotify"
    TIDAL = "tidal"
    REKORDBOX = "rekordbox"
    MANUAL = "manual"
