"""Track domain model."""

from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from set_manager.models.enums import MatchStatus, RekordboxColor, TrackSource


class Track(BaseModel):
    """Represents a single audio track with metadata from one or more sources."""

    id: UUID = Field(default_factory=uuid4)
    title: str
    artist: str
    bpm: float | None = None
    key: str | None = None
    duration: int | None = None  # seconds
    isrc: str | None = None

    # Source identifiers
    source: TrackSource = TrackSource.MANUAL
    spotify_id: str | None = None
    tidal_id: str | None = None
    rekordbox_id: int | None = None

    # Local file reference
    filepath: str | None = None

    # Matching metadata
    match_status: MatchStatus = MatchStatus.UNMATCHED

    # Optional color override (when not using section default)
    color: RekordboxColor | None = None

    # Spotify audio features (fetched on demand, cached in project file)
    energy: float | None = None        # 0.0 (calm) – 1.0 (energetic)
    danceability: float | None = None  # 0.0 – 1.0
    valence: float | None = None       # 0.0 (sad) – 1.0 (happy)

    @property
    def display_name(self) -> str:
        return f"{self.artist} - {self.title}"

    @property
    def duration_formatted(self) -> str | None:
        if self.duration is None:
            return None
        minutes, seconds = divmod(self.duration, 60)
        return f"{minutes}:{seconds:02d}"
