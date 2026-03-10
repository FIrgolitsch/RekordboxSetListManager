"""Horizontal energy timeline visualization across all tracks in a set list."""

from __future__ import annotations

from uuid import UUID

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

from set_manager.models.enums import RekordboxColor
from set_manager.models.set_list import SetList
from set_manager.models.track import Track
from set_manager.utils.constants import REKORDBOX_COLOR_HEX

_BAR_GAP = 1       # px gap between bars
_BAR_ALPHA = 200   # bar opacity (0–255)
_PLACEHOLDER_COLOR = "#888888"
_BOUNDARY_COLOR = "#555555"
_DEFAULT_BAR_COLOR = "#888888"


class SetOverviewWidget(QWidget):
    """Bar-chart energy timeline for the selected set list.

    One vertical bar per track across all sections (in section order).
    Bar height encodes ``track.energy`` (0.0–1.0).  Bar color is derived
    from the section's :class:`~set_manager.models.enums.RekordboxColor`.
    Thin vertical lines mark section boundaries.

    When no set list is selected, no tracks exist, or no energy data is
    present, a text placeholder is rendered instead.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._set_list: SetList | None = None
        self._tracks: dict[UUID, Track] = {}
        self.setMinimumHeight(80)

    # ------------------------------------------------------------------ public

    def set_set_list(
        self,
        set_list: SetList | None,
        tracks: dict[UUID, Track],
    ) -> None:
        """Update the widget with a new set list context and schedule a repaint."""
        self._set_list = set_list
        self._tracks = tracks
        self.update()

    # ------------------------------------------------------------ Qt overrides

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        self._paint(painter)
        painter.end()

    # ----------------------------------------------------------------- private

    def _paint(self, painter: QPainter) -> None:
        w, h = self.width(), self.height()

        if self._set_list is None:
            self._draw_placeholder(painter, w, h, "Select a set list to view energy curve")
            return

        # Build flat list of (track, section_color) in section order.
        # Also track which indices start a new section boundary.
        track_data: list[tuple[Track, RekordboxColor]] = []
        section_boundaries: set[int] = set()

        for section in self._set_list.sections:
            first_in_section = True
            for tid in section.track_ids:
                track = self._tracks.get(tid)
                if track is None:
                    continue
                if first_in_section and track_data:
                    section_boundaries.add(len(track_data))
                track_data.append((track, section.color))
                first_in_section = False

        if not track_data:
            self._draw_placeholder(painter, w, h, "No tracks in this set list")
            return

        has_energy = any(t.energy is not None for t, _ in track_data)
        if not has_energy:
            self._draw_placeholder(
                painter, w, h, "No energy data — use Project → Fetch Audio Features…"
            )
            return

        # Draw energy bars
        n = len(track_data)
        bar_w_f = w / n
        for i, (track, sec_color) in enumerate(track_data):
            energy = track.energy if track.energy is not None else 0.0
            bar_h = max(1, int(energy * h))
            x = int(i * bar_w_f)
            next_x = int((i + 1) * bar_w_f)
            bar_w = max(1, next_x - x - _BAR_GAP)
            y = h - bar_h

            hex_str = REKORDBOX_COLOR_HEX.get(sec_color, _DEFAULT_BAR_COLOR)
            c = QColor(hex_str)
            c.setAlpha(_BAR_ALPHA)
            painter.fillRect(x, y, bar_w, bar_h, c)

        # Draw section boundary lines
        painter.setPen(QColor(_BOUNDARY_COLOR))
        for idx in section_boundaries:
            x = int(idx * bar_w_f)
            painter.drawLine(x, 0, x, h)

    def _draw_placeholder(self, painter: QPainter, w: int, h: int, text: str) -> None:
        painter.setPen(QColor(_PLACEHOLDER_COLOR))
        painter.drawText(QRect(0, 0, w, h), Qt.AlignmentFlag.AlignCenter, text)
