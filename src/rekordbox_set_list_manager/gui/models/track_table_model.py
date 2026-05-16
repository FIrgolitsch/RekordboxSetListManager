"""QAbstractTableModel for displaying the tracks in a section."""

from __future__ import annotations

import collections.abc
from uuid import UUID

from PySide6.QtCore import (
    QAbstractTableModel,
    QMimeData,
    QModelIndex,
    QPersistentModelIndex,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor

from rekordbox_set_list_manager.models.enums import RekordboxColor
from rekordbox_set_list_manager.models.track import Track
from rekordbox_set_list_manager.utils.constants import REKORDBOX_COLOR_HEX

COLUMNS = ["#", "Title", "Artist", "BPM", "Key", "Duration", "Status"]

_CENTER = Qt.AlignmentFlag.AlignCenter
_LEFT = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
_CENTER_COLS = {0, 3, 4, 5, 6}  # #, BPM, Key, Duration, Status

_STATUS_LABELS = {
    "unmatched": "—",
    "matched": "✓",
    "manually_matched": "M",
    "conflicted": "!",
}

# Strategies that warrant a numeric score badge instead of plain "✓"
_FUZZY_STRATEGIES = {"fuzzy", "filename"}


def _status_badge(track: Track) -> str:
    """Return the text for the Status column cell."""
    status = str(track.match_status)
    if status in ("unmatched", "conflicted"):
        return _STATUS_LABELS.get(status, "?")
    if status == "manually_matched":
        return "M"
    # matched — show score % for fuzzy/filename strategies, ✓ otherwise
    strategy = track.match_strategy or ""
    if strategy in _FUZZY_STRATEGIES and track.match_score is not None:
        return f"{track.match_score * 100:.0f}%"
    return "✓"

_MIME_TYPE = "application/x-set-manager-rows"
_BG_ALPHA = 45  # background tint transparency (0-255)


def _section_bg(color: RekordboxColor | None) -> QColor | None:
    if color is None or color == RekordboxColor.NONE:
        return None
    hex_str = REKORDBOX_COLOR_HEX.get(color, "#000000")
    c = QColor(hex_str)
    c.setAlpha(_BG_ALPHA)
    return c


class TrackTableModel(QAbstractTableModel):
    """Displays the ordered tracks for the currently selected section.

    Supports internal drag-and-drop row reordering.  After a successful
    drop, :attr:`tracks_reordered` is emitted with the new ordered list
    of track IDs so callers can persist the change to the domain model.
    """

    tracks_reordered = Signal(list)  # list[UUID]
    about_to_reorder = Signal()  # fired before drag-drop reorder commits

    def __init__(self, parent=None) -> None:
        """Initialise an empty track table model."""
        super().__init__(parent)
        self._ids: list[UUID] = []
        self._tracks: dict[UUID, Track] = {}
        self._bg: QColor | None = None
        self._section_id: UUID | None = None

    # ------------------------------------------------------------------ public

    def set_section(
        self,
        track_ids: list[UUID],
        tracks: dict[UUID, Track],
        section_color: RekordboxColor | None = None,
        section_id: UUID | None = None,
    ) -> None:
        """Replace model contents with the tracks from a section."""
        self.beginResetModel()
        self._ids = list(track_ids)
        self._tracks = tracks
        self._bg = _section_bg(section_color)
        if section_id is not None:
            self._section_id = section_id
        self.endResetModel()

    def clear(self) -> None:
        """Clear all tracks from the model."""
        self.beginResetModel()
        self._ids = []
        self._bg = None
        self.endResetModel()

    def track_id_at(self, row: int) -> UUID | None:
        """Return the track UUID at *row*, or None if out of range."""
        if 0 <= row < len(self._ids):
            return self._ids[row]
        return None

    def ordered_ids(self) -> list[UUID]:
        """Return a copy of the current track-ID list in display order."""
        return list(self._ids)

    # ------------------------------------------------------------ Qt overrides

    def rowCount(self, parent=QModelIndex()) -> int:
        """Return the number of rows in the model."""
        return 0 if parent.isValid() else len(self._ids)

    def columnCount(self, parent=QModelIndex()) -> int:
        """Return the number of columns in the model."""
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section: int, orientation, role=Qt.ItemDataRole.DisplayRole):
        """Return column header labels for the horizontal header."""
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        return None

    def data(
        self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole
    ) -> object:
        """Return display data or decoration for the given model index."""
        if not index.isValid():
            return None
        row, col = index.row(), index.column()
        if row >= len(self._ids):
            return None
        track = self._tracks.get(self._ids[row])
        if track is None:
            return None

        if role == Qt.ItemDataRole.DisplayRole:
            return self._display(row, col, track)
        if role == Qt.ItemDataRole.BackgroundRole:
            return self._bg
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int(_CENTER if col in _CENTER_COLS else _LEFT)
        return None

    def _display(self, row: int, col: int, track: Track) -> str:
        match col:
            case 0:
                return str(row + 1)
            case 1:
                return track.title
            case 2:
                return track.artist
            case 3:
                return f"{track.bpm:.1f}" if track.bpm is not None else "—"
            case 4:
                return track.key or "—"
            case 5:
                return track.duration_formatted or "—"
            case 6:
                return _status_badge(track)
        return ""

    # ---------------------------------------------------------- drag and drop

    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlag:
        """Return item flags; draggable for valid indexes, drop-enabled otherwise."""
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.isValid():
            return base | Qt.ItemFlag.ItemIsDragEnabled
        return base | Qt.ItemFlag.ItemIsDropEnabled

    def supportedDragActions(self) -> Qt.DropAction:
        """Return the supported drag actions for this model."""
        return Qt.DropAction.MoveAction

    def supportedDropActions(self) -> Qt.DropAction:
        """Return the supported drop actions for this model."""
        return Qt.DropAction.MoveAction

    def mimeTypes(self) -> list[str]:
        """Return the MIME types accepted for drag-and-drop."""
        return [_MIME_TYPE]

    def mimeData(self, indexes: collections.abc.Sequence[QModelIndex]) -> QMimeData:
        """Encode the selected row indices into MIME data for drag operations."""
        mime = QMimeData()
        rows = sorted({i.row() for i in indexes if i.isValid()})
        prefix = f"{self._section_id}:" if self._section_id else ""
        mime.setData(_MIME_TYPE, (prefix + ",".join(str(r) for r in rows)).encode())
        return mime

    def dropMimeData(
        self,
        data: QMimeData,
        action: Qt.DropAction,
        row: int,
        column: int,
        parent: QModelIndex | QPersistentModelIndex,
    ) -> bool:
        """Handle a drop event and reorder tracks accordingly."""
        if action != Qt.DropAction.MoveAction:
            return False
        if not data.hasFormat(_MIME_TYPE):
            return False

        raw = data.data(_MIME_TYPE).toStdString()

        # Strip section prefix if present; reject cross-section drops (handled by view)
        if ":" in raw:
            src_id, row_part = raw.split(":", 1)
            if self._section_id and src_id != str(self._section_id):
                return False
            raw = row_part

        src_rows = [int(r) for r in raw.split(",") if r.strip().isdigit()]
        if not src_rows:
            return False

        self.about_to_reorder.emit()  # capture undo state before mutating
        dest = row if row >= 0 else len(self._ids)

        moving = [self._ids[r] for r in src_rows if 0 <= r < len(self._ids)]
        src_set = set(src_rows)
        remaining = [tid for i, tid in enumerate(self._ids) if i not in src_set]

        removed_before = sum(1 for r in src_rows if r < dest)
        dest = max(0, min(dest - removed_before, len(remaining)))

        self.beginResetModel()
        for i, tid in enumerate(moving):
            remaining.insert(dest + i, tid)
        self._ids = remaining
        self.endResetModel()

        self.tracks_reordered.emit(list(self._ids))
        return True
