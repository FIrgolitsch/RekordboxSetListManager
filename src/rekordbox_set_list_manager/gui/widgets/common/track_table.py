"""Filter proxy model for the track table.

:class:`TrackFilterProxyModel` is imported by :mod:`multi_section_view`.
"""

from __future__ import annotations

from PySide6.QtCore import (
    QModelIndex,
    QPersistentModelIndex,
    QSortFilterProxyModel,
    Qt,
)


def _parse_duration(s: str) -> int:
    """Parse "mm:ss" or "h:mm:ss" to total seconds; returns -1 on failure."""
    parts = s.split(":")
    try:
        return sum(int(p) * 60 ** (len(parts) - 1 - i) for i, p in enumerate(parts))
    except ValueError:
        return -1


class TrackFilterProxyModel(QSortFilterProxyModel):
    """Proxy that adds text filtering and numeric-aware sorting."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._filter_text = ""
        self.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

    def set_filter_text(self, text: str) -> None:
        self._filter_text = text.lower()
        self.invalidateFilter()

    # ── filtering ──

    def filterAcceptsRow(
        self, source_row: int, source_parent: QModelIndex | QPersistentModelIndex
    ) -> bool:
        if not self._filter_text:
            return True
        model = self.sourceModel()
        for col in (1, 2):  # Title, Artist
            idx = model.index(source_row, col, source_parent)
            val = (model.data(idx, Qt.ItemDataRole.DisplayRole) or "").lower()
            if self._filter_text in val:
                return True
        return False

    # ── sorting ──

    def lessThan(
        self, left: QModelIndex | QPersistentModelIndex, right: QModelIndex | QPersistentModelIndex
    ) -> bool:
        col = left.column()
        model = self.sourceModel()
        lv = model.data(left, Qt.ItemDataRole.DisplayRole) or ""
        rv = model.data(right, Qt.ItemDataRole.DisplayRole) or ""

        if col == 0:  # Row # — sort as integer
            try:
                return int(lv) < int(rv)
            except ValueError:
                pass
        elif col == 3:  # noqa: PLR2004  # BPM column index
            lf = float(lv) if lv not in ("—", "") else -1.0
            rf = float(rv) if rv not in ("—", "") else -1.0
            return lf < rf
        elif col == 5:  # noqa: PLR2004  # Duration column index
            return _parse_duration(lv) < _parse_duration(rv)

        return lv.lower() < rv.lower()
