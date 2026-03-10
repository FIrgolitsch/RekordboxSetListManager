"""Section domain model."""

from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from set_manager.models.enums import RekordboxColor, SectionType


class Section(BaseModel):
    """An ordered group of tracks within a set list, with an assigned color."""

    id: UUID = Field(default_factory=uuid4)
    name: str
    section_type: SectionType = SectionType.GENERAL
    color: RekordboxColor = RekordboxColor.NONE
    track_ids: list[UUID] = Field(default_factory=list)
    # Keys are str(track_id); value is the transition note after that track.
    transition_notes: dict[str, str] = Field(default_factory=dict)

    def add_track(self, track_id: UUID) -> None:
        if track_id not in self.track_ids:
            self.track_ids.append(track_id)

    def remove_track(self, track_id: UUID) -> None:
        self.track_ids = [tid for tid in self.track_ids if tid != track_id]

    def move_track(self, track_id: UUID, new_index: int) -> None:
        if track_id not in self.track_ids:
            raise ValueError(f"Track {track_id} not in section '{self.name}'")
        self.track_ids.remove(track_id)
        self.track_ids.insert(new_index, track_id)

    @property
    def track_count(self) -> int:
        return len(self.track_ids)
