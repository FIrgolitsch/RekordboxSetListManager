"""Project-wide constants and lookup tables."""

from set_manager.models.enums import RekordboxColor, SectionType

# Human-readable display names for each SectionType
SECTION_TYPE_LABELS: dict[SectionType, str] = {
    SectionType.OPENER: "Opener",
    SectionType.WARM_UP: "Warm Up",
    SectionType.BUILD: "Build",
    SectionType.PEAK: "Peak",
    SectionType.AFTER_PEAK: "After Peak",
    SectionType.CLOSING: "Closing",
    SectionType.BREAK: "Break",
    SectionType.GENERAL: "General",
}

# Default Rekordbox color assigned to each SectionType.
# These assignments create a natural energy-arc colour coding.
# Color values will be verified empirically against Rekordbox 7.2 in Phase 2.
DEFAULT_SECTION_COLORS: dict[SectionType, RekordboxColor] = {
    SectionType.OPENER: RekordboxColor.AQUA,
    SectionType.WARM_UP: RekordboxColor.GREEN,
    SectionType.BUILD: RekordboxColor.YELLOW,
    SectionType.PEAK: RekordboxColor.RED,
    SectionType.AFTER_PEAK: RekordboxColor.ORANGE,
    SectionType.CLOSING: RekordboxColor.BLUE,
    SectionType.BREAK: RekordboxColor.PURPLE,
    SectionType.GENERAL: RekordboxColor.NONE,
}

# Hex string representations for UI display (e.g. in color swatches).
# Format: "#RRGGBB"
REKORDBOX_COLOR_HEX: dict[RekordboxColor, str] = {
    RekordboxColor.NONE: "#000000",
    RekordboxColor.PINK: "#F870F8",
    RekordboxColor.RED: "#F87070",
    RekordboxColor.ORANGE: "#FFA064",
    RekordboxColor.YELLOW: "#F8E550",
    RekordboxColor.GREEN: "#1EE12B",
    RekordboxColor.AQUA: "#10E4DC",
    RekordboxColor.BLUE: "#1E50F0",
    RekordboxColor.PURPLE: "#9828F0",
}

# Extension used for project save files
PROJECT_FILE_EXTENSION = ".setmgr"
