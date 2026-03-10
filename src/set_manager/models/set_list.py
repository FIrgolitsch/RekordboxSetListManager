"""SetList domain model."""

import datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from set_manager.models.section import Section


class SetList(BaseModel):
    """An ordered collection of sections representing a complete DJ set."""

    id: UUID = Field(default_factory=uuid4)
    name: str
    date: datetime.date | None = None
    venue: str | None = None
    sections: list[Section] = Field(default_factory=list)

    def add_section(self, section: Section) -> None:
        self.sections.append(section)

    def remove_section(self, section_id: UUID) -> None:
        self.sections = [s for s in self.sections if s.id != section_id]

    def get_section(self, section_id: UUID) -> Section | None:
        return next((s for s in self.sections if s.id == section_id), None)

    def move_section(self, section_id: UUID, new_index: int) -> None:
        section = self.get_section(section_id)
        if section is None:
            raise ValueError(f"Section {section_id} not found in set list '{self.name}'")
        self.sections.remove(section)
        self.sections.insert(new_index, section)

    @property
    def all_track_ids(self) -> list[UUID]:
        """All track IDs across all sections, in order."""
        return [tid for section in self.sections for tid in section.track_ids]

    @property
    def total_track_count(self) -> int:
        return sum(s.track_count for s in self.sections)
