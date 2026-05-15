"""Custom QTableView for section track lists with cross-section drag-and-drop."""

from __future__ import annotations

from uuid import UUID

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QTableView

# Shared MIME type used by SectionTableView and SectionBlock for drag payloads.
MIME_TYPE = "application/x-set-manager-rows"


class SectionTableView(QTableView):
    """QTableView variant that:

    - Locks viewport scrolling (all rows always fit)
    - Intercepts cross-section drop events and emits :attr:`cross_section_drop`
      instead of mutating the model directly
    """

    cross_section_drop = Signal(str, list, int)

    def __init__(self, section_id: UUID, parent=None) -> None:
        super().__init__(parent)
        self._section_id_str = str(section_id)

    def wheelEvent(self, event) -> None:
        event.ignore()  # let the outer scroll area handle it

    def scrollContentsBy(self, dx: int, dy: int) -> None:
        pass  # lock viewport — all rows always fit, nothing should scroll

    def dropEvent(self, event) -> None:
        mime = event.mimeData()
        if mime.hasFormat(MIME_TYPE):
            raw = bytes(mime.data(MIME_TYPE)).decode()
            if ":" in raw:
                src_id, rows_str = raw.split(":", 1)
                if src_id != self._section_id_str:
                    rows = [int(r) for r in rows_str.split(",") if r.strip().isdigit()]
                    idx = self.indexAt(event.position().toPoint())
                    dest_row = idx.row() if idx.isValid() else self.model().rowCount()
                    self.cross_section_drop.emit(src_id, rows, dest_row)
                    event.acceptProposedAction()
                    return
        super().dropEvent(event)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(MIME_TYPE):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasFormat(MIME_TYPE):
            event.acceptProposedAction()
        super().dragMoveEvent(event)
