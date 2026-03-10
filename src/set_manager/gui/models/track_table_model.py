"""QAbstractTableModel for displaying the tracks in a section."""

from __future__ import annotations

from uuid import UUID

from PySide6.QtCore import QAbstractTableModel, QMimeData, QModelIndex, Qt, Signal
from PySide6.QtGui import QColor

from set_manager.models.enums import RekordboxColor
from set_manager.models.track import Track
from set_manager.utils.constants import REKORDBOX_COLOR_HEX

COLUMNS = ["#", "Title", "Artist", "BPM", "Key", "Duration", "Status",
           "Energy", "Danceability", "Valence"]

_CENTER = Qt.AlignmentFlag.AlignCenter
_LEFT = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
_CENTER_COLS = {0, 3, 4, 5, 6, 7, 8, 9}  # #, BPM, Key, Duration, Status, Energy, Dance, Valence

_STATUS_LABELS = {
    "unmatched": "—",
    "matched": "✓",
    "manually_matched": "M",
    "conflicted": "!",
}

_MIME_TYPE = "application/x-set-manager-rows"
_BG_ALPHA = 45  # background tint transparency (0–255)


def _section_bg(color: RekordboxColor | None) -> QColor | None:
    if color is None or color == RekordboxColor.NONE:
        return None
    hex_str = REKORDBOX_COLOR_HEX.get(color, "#000000")
    c = QColor(hex_str)
    c.setAlpha(_BG_ALPHA)
    return c


def _energy_bg(energy: float | None) -> QColor:
    """Return a tinted QColor encoding the energy level for the Energy column."""
    if energy is None:
        hex_str = "#9E9E9E"  # gray — no data
    elif energy > 0.66:
        hex_str = "#4CAF50"  # green — high energy
    elif energy > 0.33:
        hex_str = "#FFC107"  # amber — medium energy
    else:
        hex_str = "#2196F3"  # blue — low energy
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
        super().__init__(parent)
        self._ids: list[UUID] = []
        self._tracks: dict[UUID, Track] = {}
        self._bg: QColor | None = None

    # ------------------------------------------------------------------ public

    def set_section(
        self,
        track_ids: list[UUID],
        tracks: dict[UUID, Track],
        section_color: RekordboxColor | None = None,
    ) -> None:
        self.beginResetModel()
        self._ids = list(track_ids)
        self._tracks = tracks
        self._bg = _section_bg(section_color)
        self.endResetModel()

    def clear(self) -> None:
        self.beginResetModel()
        self._ids = []
        self._bg = None
        self.endResetModel()

    def track_id_at(self, row: int) -> UUID | None:
        if 0 <= row < len(self._ids):
            return self._ids[row]
        return None

    def ordered_ids(self) -> list[UUID]:
        return list(self._ids)

    # ------------------------------------------------------------ Qt overrides

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._ids)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section: int, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
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
            if col == 7:  # Energy column gets energy-coded color
                return _energy_bg(track.energy)
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
                return _STATUS_LABELS.get(str(track.match_status), "?")
            case 7:
                return f"{int(track.energy * 100)}%" if track.energy is not None else "—"
            case 8:
                return (
                    f"{int(track.danceability * 100)}%"
                    if track.danceability is not None
                    else "—"
                )
            case 9:
                return f"{int(track.valence * 100)}%" if track.valence is not None else "—"
        return ""

    # ---------------------------------------------------------- drag and drop

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.isValid():
            return base | Qt.ItemFlag.ItemIsDragEnabled
        return base | Qt.ItemFlag.ItemIsDropEnabled

    def supportedDragActions(self) -> Qt.DropAction:
        return Qt.DropAction.MoveAction

    def supportedDropActions(self) -> Qt.DropAction:
        return Qt.DropAction.MoveAction

    def mimeTypes(self) -> list[str]:
        return [_MIME_TYPE]

    def mimeData(self, indexes: list[QModelIndex]) -> QMimeData:
        mime = QMimeData()
        rows = sorted({i.row() for i in indexes if i.isValid()})
        mime.setData(_MIME_TYPE, ",".join(str(r) for r in rows).encode())
        return mime

    def dropMimeData(
        self,
        data: QMimeData,
        action: Qt.DropAction,
        row: int,
        column: int,
        parent: QModelIndex,
    ) -> bool:
        if action != Qt.DropAction.MoveAction:
            return False
        if not data.hasFormat(_MIME_TYPE):
            return False

        raw = bytes(data.data(_MIME_TYPE)).decode()
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
