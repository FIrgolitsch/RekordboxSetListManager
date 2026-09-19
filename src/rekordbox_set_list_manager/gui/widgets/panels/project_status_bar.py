"""Statusbar widget projecting project track/duration/unmatched totals."""

from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from rekordbox_set_list_manager.models.enums import MatchStatus
from rekordbox_set_list_manager.models.project import Project
from rekordbox_set_list_manager.utils.formatting import format_total_duration


class ProjectStatusBar(QWidget):
    """Statusbar projection: unique track count, total duration, unmatched count."""

    def __init__(self, parent=None) -> None:
        """Initialise the three status labels with separators."""
        super().__init__(parent)
        self._status_tracks = QLabel()
        self._status_duration = QLabel()
        self._status_unmatched = QLabel()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._status_tracks)
        layout.addWidget(QLabel("  |  "))
        layout.addWidget(self._status_duration)
        layout.addWidget(QLabel("  |  "))
        layout.addWidget(self._status_unmatched)

    def update_for(self, project: Project | None) -> None:
        """Update the labels to reflect *project* (or the empty state).

        Parameters
        ----------
        project : Project | None
            The project to summarise, or ``None`` to show the empty state.

        """
        if project is None:
            self._status_tracks.setText("No project")
            self._status_duration.setText("")
            self._status_unmatched.setText("")
            return
        all_ids: set = set()
        unmatched_ids: set = set()
        total_dur = 0
        for sec in project.sections:
            for tid in sec.track_ids:
                all_ids.add(tid)
                track = project.get_track(tid)
                if track:
                    total_dur += track.duration or 0
                    if track.match_status == MatchStatus.UNMATCHED:
                        unmatched_ids.add(tid)
        self._status_tracks.setText(f"Tracks: {len(all_ids)}")
        self._status_duration.setText(f"Duration: {format_total_duration(total_dur)}")
        # Unique tracks, like "Tracks:" — a track in two sections counts once.
        self._status_unmatched.setText(f"Unmatched: {len(unmatched_ids)}")
