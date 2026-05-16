"""Project domain model — top-level container for all set-manager data."""

from __future__ import annotations

import datetime
from typing import cast
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator

from rekordbox_set_list_manager.models.enums import RekordboxColor, SectionType
from rekordbox_set_list_manager.models.section import Section
from rekordbox_set_list_manager.models.section_name_theme import SectionNameTheme
from rekordbox_set_list_manager.models.track import Track
from rekordbox_set_list_manager.utils.constants import DEFAULT_SECTION_COLORS


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

    # The single set — sections stored directly on the project.
    sections: list[Section] = Field(default_factory=list)

    # Streaming playlist IDs from which this set was imported.
    spotify_playlist_id: str | None = None
    tidal_playlist_id: str | None = None

    # Master track store: track_id → Track.  Sections reference tracks by ID.
    tracks: dict[UUID, Track] = Field(default_factory=dict)

    # Override the default color per section type; used when creating new sections.
    section_color_map: dict[SectionType, RekordboxColor] = Field(
        default_factory=_default_section_color_map
    )

    # User-defined section name themes, created and edited via the GUI.
    themes: list[SectionNameTheme] = Field(default_factory=list)

    # ---------------------------------------------------------------- migration

    @model_validator(mode="before")
    @classmethod
    def _migrate_set_lists(cls, data: object) -> object:
        """Migrate old format: project.set_lists[0] → project.sections."""
        if isinstance(data, dict) and "set_lists" in data:
            d = cast("dict[str, object]", data)
            set_lists = d.pop("set_lists", [])
            if set_lists and isinstance(set_lists, list):
                first = set_lists[0]
                if isinstance(first, dict):
                    fd = cast("dict[str, object]", first)
                    if "sections" not in d:
                        d["sections"] = fd.get("sections", [])
                    if not d.get("spotify_playlist_id"):
                        d["spotify_playlist_id"] = fd.get("spotify_playlist_id")
                    if not d.get("tidal_playlist_id"):
                        d["tidal_playlist_id"] = fd.get("tidal_playlist_id")
        return data

    # ------------------------------------------------------------------ tracks

    def add_track(self, track: Track) -> None:
        """Add *track* to the project track dictionary.

        Parameters
        ----------
        track : Track
            The track to register; stored under ``track.id``.

        """
        self.tracks[track.id] = track

    def remove_track(self, track_id: UUID) -> None:
        """Remove the track and all its section references from the project.

        Parameters
        ----------
        track_id : UUID
            ID of the track to delete from the track store and all sections.

        """
        self.tracks.pop(track_id, None)
        for section in self.sections:
            section.remove_track(track_id)

    def get_track(self, track_id: UUID) -> Track | None:
        """Return the track with *track_id*, or None if not found.

        Parameters
        ----------
        track_id : UUID
            The ID of the track to look up.

        Returns
        -------
        Track | None
            The matching track, or ``None`` if no track has that ID.

        """
        return self.tracks.get(track_id)

    # --------------------------------------------------------------- sections

    def add_section(self, section: Section) -> None:
        """Append *section* to the project's section list.

        Parameters
        ----------
        section : Section
            The section to append.

        """
        self.sections.append(section)

    def remove_section(self, section_id: UUID) -> None:
        """Remove the section with *section_id* from the project.

        Parameters
        ----------
        section_id : UUID
            ID of the section to remove.

        """
        self.sections = [s for s in self.sections if s.id != section_id]

    def get_section(self, section_id: UUID) -> Section | None:
        """Return the section with *section_id*, or None if not found.

        Parameters
        ----------
        section_id : UUID
            The ID of the section to look up.

        Returns
        -------
        Section | None
            The matching section, or ``None`` if not found.

        """
        return next((s for s in self.sections if s.id == section_id), None)

    def move_section(self, section_id: UUID, new_index: int) -> None:
        """Move the section with *section_id* to *new_index* in the list.

        Parameters
        ----------
        section_id : UUID
            ID of the section to reposition.
        new_index : int
            Target zero-based position in the sections list.

        """
        section = self.get_section(section_id)
        if section is None:
            raise ValueError(f"Section {section_id} not found in project '{self.name}'")
        self.sections.remove(section)
        self.sections.insert(new_index, section)

    @property
    def all_track_ids(self) -> list[UUID]:
        """All track IDs across all sections, in order."""
        return [tid for section in self.sections for tid in section.track_ids]

    @property
    def total_track_count(self) -> int:
        """Return the total number of tracks across all sections."""
        return sum(s.track_count for s in self.sections)

    # ------------------------------------------------------------------ themes

    def add_theme(self, theme: SectionNameTheme) -> None:
        """Append *theme* to the project's theme list.

        Parameters
        ----------
        theme : SectionNameTheme
            The theme preset to add.

        """
        self.themes.append(theme)

    def remove_theme(self, theme_name: str) -> None:
        """Remove the theme named *theme_name* from the project.

        Parameters
        ----------
        theme_name : str
            Exact name of the theme to remove.

        """
        self.themes = [t for t in self.themes if t.name != theme_name]

    def get_theme(self, theme_name: str) -> SectionNameTheme | None:
        """Return the theme named *theme_name*, or None if not found.

        Parameters
        ----------
        theme_name : str
            Exact name of the theme to look up.

        Returns
        -------
        SectionNameTheme | None
            The matching theme, or ``None`` if not found.

        """
        return next((t for t in self.themes if t.name == theme_name), None)

    def apply_theme(self, theme_name: str) -> None:
        """Rename sections using the named theme.

        Only sections whose SectionType appears in the theme's ``names`` mapping
        are renamed; all others are left unchanged.

        Parameters
        ----------
        theme_name : str
            Name of the theme to apply.

        Raises
        ------
        ValueError
            If no theme with *theme_name* exists in the project.

        """
        theme = self.get_theme(theme_name)
        if theme is None:
            raise ValueError(f"Theme '{theme_name}' not found")
        for section in self.sections:
            themed_name = theme.display_name_for(section.section_type)
            if themed_name is not None:
                section.name = themed_name

    # -------------------------------------------------------------------- misc

    def touch(self) -> None:
        """Update the `updated_at` timestamp to now."""
        self.updated_at = datetime.datetime.now(datetime.UTC)

    def default_color_for(self, section_type: SectionType) -> RekordboxColor:
        """Return the configured default color for *section_type*.

        Parameters
        ----------
        section_type : SectionType
            The section type whose default color to retrieve.

        Returns
        -------
        RekordboxColor
            The mapped default color, or ``RekordboxColor.NONE`` if not set.

        """
        return self.section_color_map.get(section_type, RekordboxColor.NONE)
