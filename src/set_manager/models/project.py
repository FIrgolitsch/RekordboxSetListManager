"""Project domain model — top-level container for all set-manager data."""

import datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from set_manager.models.enums import RekordboxColor, SectionType
from set_manager.models.section_name_theme import SectionNameTheme
from set_manager.models.set_list import SetList
from set_manager.models.track import Track
from set_manager.utils.constants import DEFAULT_SECTION_COLORS


def _default_section_color_map() -> dict[SectionType, RekordboxColor]:
    return dict(DEFAULT_SECTION_COLORS)


class Project(BaseModel):
    """Top-level container: one project per save file."""

    id: UUID = Field(default_factory=uuid4)
    name: str
    created_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC)
    )
    updated_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC)
    )

    set_lists: list[SetList] = Field(default_factory=list)

    # Master track store: track_id → Track.  Sections reference tracks by ID.
    tracks: dict[UUID, Track] = Field(default_factory=dict)

    # Override the default color per section type; used when creating new sections.
    section_color_map: dict[SectionType, RekordboxColor] = Field(
        default_factory=_default_section_color_map
    )

    # User-defined section name themes, created and edited via the GUI.
    themes: list[SectionNameTheme] = Field(default_factory=list)

    # ------------------------------------------------------------------ tracks

    def add_track(self, track: Track) -> None:
        self.tracks[track.id] = track

    def remove_track(self, track_id: UUID) -> None:
        self.tracks.pop(track_id, None)
        for set_list in self.set_lists:
            for section in set_list.sections:
                section.remove_track(track_id)

    def get_track(self, track_id: UUID) -> Track | None:
        return self.tracks.get(track_id)

    # --------------------------------------------------------------- set lists

    def add_set_list(self, set_list: SetList) -> None:
        self.set_lists.append(set_list)

    def remove_set_list(self, set_list_id: UUID) -> None:
        self.set_lists = [sl for sl in self.set_lists if sl.id != set_list_id]

    def get_set_list(self, set_list_id: UUID) -> SetList | None:
        return next((sl for sl in self.set_lists if sl.id == set_list_id), None)

    # ------------------------------------------------------------------ themes

    def add_theme(self, theme: SectionNameTheme) -> None:
        self.themes.append(theme)

    def remove_theme(self, theme_name: str) -> None:
        self.themes = [t for t in self.themes if t.name != theme_name]

    def get_theme(self, theme_name: str) -> SectionNameTheme | None:
        return next((t for t in self.themes if t.name == theme_name), None)

    def apply_theme_to_set_list(self, theme_name: str, set_list_id: UUID) -> None:
        """Rename sections in *set_list* using the named theme.

        Only sections whose SectionType appears in the theme's ``names`` mapping
        are renamed; all others are left unchanged.

        Raises :class:`ValueError` if the theme or set list does not exist.
        """
        theme = self.get_theme(theme_name)
        if theme is None:
            raise ValueError(f"Theme '{theme_name}' not found")
        set_list = self.get_set_list(set_list_id)
        if set_list is None:
            raise ValueError(f"Set list {set_list_id} not found")
        for section in set_list.sections:
            themed_name = theme.display_name_for(section.section_type)
            if themed_name is not None:
                section.name = themed_name

    # -------------------------------------------------------------------- misc

    def touch(self) -> None:
        """Update the `updated_at` timestamp to now."""
        self.updated_at = datetime.datetime.now(datetime.UTC)

    def default_color_for(self, section_type: SectionType) -> RekordboxColor:
        return self.section_color_map.get(section_type, RekordboxColor.NONE)
