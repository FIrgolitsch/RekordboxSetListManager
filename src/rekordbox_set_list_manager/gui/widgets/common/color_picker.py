"""Reusable color swatch picker widget for Rekordbox track/section colors."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget

from rekordbox_set_list_manager.models.enums import RekordboxColor
from rekordbox_set_list_manager.utils.constants import REKORDBOX_COLOR_HEX


class ColorPickerWidget(QWidget):
    """A row of colored circle buttons for picking a :class:`RekordboxColor`.

    Emits ``color_selected`` when the user clicks a swatch.  The initially
    selected color (if any) is highlighted on construction.
    """

    color_selected = Signal(object)  # RekordboxColor | None

    def __init__(
        self,
        selected: RekordboxColor | None = None,
        parent=None,
    ) -> None:
        """Build the color-picker widget with *selected* pre-highlighted."""
        super().__init__(parent)
        self._selected = selected
        self._buttons: dict[RekordboxColor, QPushButton] = {}

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)

        for rc in RekordboxColor:
            if rc == RekordboxColor.NONE:
                continue
            btn = QPushButton()
            btn.setFixedSize(22, 22)
            btn.setToolTip(rc.name.title())
            btn.clicked.connect(lambda checked=False, c=rc: self._on_picked(c))
            self._buttons[rc] = btn
            row.addWidget(btn)

        row.addStretch()
        self._refresh_styles()

    def selected_color(self) -> RekordboxColor | None:
        """Return the currently selected color, or None if none is selected."""
        return self._selected

    def set_selected(self, color: RekordboxColor | None) -> None:
        """Set the selected color and refresh button styles."""
        self._selected = color
        self._refresh_styles()

    # ------------------------------------------------------------------

    def _on_picked(self, color: RekordboxColor) -> None:
        self._selected = color
        self._refresh_styles()
        self.color_selected.emit(color)

    def _refresh_styles(self) -> None:
        for rc, btn in self._buttons.items():
            hex_color = REKORDBOX_COLOR_HEX.get(rc, "#888")
            border = "white" if rc == self._selected else "transparent"
            btn.setStyleSheet(
                f"QPushButton {{ background-color: {hex_color}; "
                f"border: 2px solid {border}; border-radius: 11px; }} "
                f"QPushButton:hover {{ border: 2px solid white; }}"
            )
