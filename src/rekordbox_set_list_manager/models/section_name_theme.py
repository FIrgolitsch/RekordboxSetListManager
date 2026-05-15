"""SectionNameTheme model — user-defined section display name presets."""

from pydantic import BaseModel, Field

from rekordbox_set_list_manager.models.enums import SectionType


class SectionNameTheme(BaseModel):
    """A named set of display names for each SectionType.

    Created and edited entirely through the GUI; stored in the project file.
    Applying a theme renames the matching sections in a project without changing
    their SectionType or color.

    Example::

        SectionNameTheme(
            name="Dawn to Dusk",
            names={
                SectionType.OPENER:     "Dawn",
                SectionType.WARM_UP:    "Morning",
                SectionType.BUILD:      "Midday",
                SectionType.PEAK:       "Afternoon",
                SectionType.AFTER_PEAK: "Dusk",
                SectionType.CLOSING:    "Twilight",
            },
        )
    """

    name: str
    # Partial mappings are allowed — section types absent from the dict keep their name.
    names: dict[SectionType, str] = Field(default_factory=dict)

    def display_name_for(self, section_type: SectionType) -> str | None:
        """Return the themed name for *section_type*, or ``None`` if not mapped."""
        return self.names.get(section_type)
