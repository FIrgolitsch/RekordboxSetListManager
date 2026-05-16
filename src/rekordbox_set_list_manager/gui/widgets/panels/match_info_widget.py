"""Read-only widget displaying Rekordbox match metadata for a selected track."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QLabel, QVBoxLayout, QWidget

_STATUS_DISPLAY = {
    "unmatched": "Unmatched",
    "matched": "Matched",
    "manually_matched": "Manually matched",
    "conflicted": "Conflicted",
}

_STRATEGY_DISPLAY = {
    "isrc": "ISRC (exact)",
    "exact": "Exact title/artist",
    "fuzzy": "Fuzzy",
    "filename": "Filename",
    "none": "—",
}

_FIELDS = (
    "Status", "Source", "Strategy", "Score",
    "Streaming URI", "File", "BPM", "Key", "ISRC", "Rekordbox ID",
)


class MatchInfoWidget(QWidget):
    """Read-only form showing match metadata for the selected track."""

    def __init__(self, parent=None) -> None:
        """Initialise the match info widget with empty labels."""
        super().__init__(parent)

        grid = QGridLayout()
        grid.setSpacing(3)
        grid.setColumnStretch(1, 1)
        self._vals: dict[str, QLabel] = {}
        for i, field in enumerate(_FIELDS):
            lbl = QLabel(field + ":")
            val = QLabel("—")
            val.setWordWrap(True)
            if field == "Streaming URI":
                val.setOpenExternalLinks(True)
                val.setTextInteractionFlags(
                    Qt.TextInteractionFlag.TextBrowserInteraction
                )
            grid.addWidget(lbl, i, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(val, i, 1, Qt.AlignmentFlag.AlignTop)
            self._vals[field] = val

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.addLayout(grid)
        layout.addStretch()

    def set_track(self, track) -> None:
        """Update display labels to show match metadata for *track*."""
        if track is None:
            for val in self._vals.values():
                val.setText("—")
            return
        self._vals["Status"].setText(
            _STATUS_DISPLAY.get(str(track.match_status), str(track.match_status))
        )
        self._vals["Source"].setText(str(track.source).capitalize())
        strategy = track.match_strategy or "none"
        self._vals["Strategy"].setText(_STRATEGY_DISPLAY.get(strategy, strategy))
        if track.match_score is not None:
            self._vals["Score"].setText(f"{track.match_score * 100:.0f}%")
        else:
            self._vals["Score"].setText("—")
        uri_label = self._streaming_uri_html(track)
        self._vals["Streaming URI"].setText(uri_label)
        self._vals["File"].setText(Path(track.filepath).name if track.filepath else "—")
        self._vals["BPM"].setText(f"{track.bpm:.1f}" if track.bpm is not None else "—")
        self._vals["Key"].setText(track.key or "—")
        self._vals["ISRC"].setText(track.isrc or "—")
        self._vals["Rekordbox ID"].setText(
            str(track.rekordbox_id) if track.rekordbox_id else "—"
        )

    @staticmethod
    def _streaming_uri_html(track) -> str:
        source = str(track.source).lower()
        if source == "spotify" and track.spotify_id:
            url = f"https://open.spotify.com/track/{track.spotify_id}"
            return f'<a href="{url}">Open in Spotify</a>'
        if source == "tidal" and track.tidal_id:
            url = f"https://tidal.com/browse/track/{track.tidal_id}"
            return f'<a href="{url}">Open in Tidal</a>'
        return "—"
