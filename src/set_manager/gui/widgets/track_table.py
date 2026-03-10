"""Center panel: table view of tracks in the selected section."""

from __future__ import annotations

from uuid import UUID

from PySide6.QtCore import QModelIndex, QSortFilterProxyModel, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLineEdit,
    QMenu,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from set_manager.gui.models.track_table_model import TrackTableModel
from set_manager.models.enums import RekordboxColor
from set_manager.models.section import Section
from set_manager.models.set_list import SetList
from set_manager.models.track import Track


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

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
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

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        col = left.column()
        model = self.sourceModel()
        lv = model.data(left, Qt.ItemDataRole.DisplayRole) or ""
        rv = model.data(right, Qt.ItemDataRole.DisplayRole) or ""

        if col == 0:  # Row # — sort as integer
            try:
                return int(lv) < int(rv)
            except ValueError:
                pass
        elif col == 3:  # BPM — float
            lf = float(lv) if lv not in ("—", "") else -1.0
            rf = float(rv) if rv not in ("—", "") else -1.0
            return lf < rf
        elif col == 5:  # Duration — mm:ss
            return _parse_duration(lv) < _parse_duration(rv)
        elif col in (7, 8, 9):  # Energy / Danceability / Valence — %
            lf = float(lv.rstrip("%")) if lv not in ("—", "") else -1.0
            rf = float(rv.rstrip("%")) if rv not in ("—", "") else -1.0
            return lf < rf

        return lv.lower() < rv.lower()


class TrackTable(QWidget):
    """Wraps a QTableView with a TrackTableModel and a filter proxy.

    Emits :attr:`about_to_modify` immediately before any mutation so the
    MainWindow can push an undo snapshot.

    Emits :attr:`section_modified` after any change (deletion, reorder) so
    the MainWindow can mark the project dirty and update the status bar.

    Emits :attr:`track_selected` (``UUID | None``) whenever the selection
    changes, so downstream widgets (e.g. TransitionNoteWidget) can update.
    """

    about_to_modify = Signal()  # emitted before any mutation
    section_modified = Signal()
    add_track_requested = Signal()
    track_selected = Signal(object)  # UUID | None

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._section: Section | None = None
        self._set_list: SetList | None = None
        self._tracks: dict[UUID, Track] = {}

        self._model = TrackTableModel()
        self._model.tracks_reordered.connect(self._on_tracks_reordered)
        # Forward the model's pre-reorder signal so MainWindow can undo.
        self._model.about_to_reorder.connect(self.about_to_modify)

        self._proxy = TrackFilterProxyModel()
        self._proxy.setSourceModel(self._model)

        self._filter = QLineEdit()
        self._filter.setPlaceholderText("Filter tracks by title or artist…")
        self._filter.setClearButtonEnabled(True)
        self._filter.textChanged.connect(self._proxy.set_filter_text)

        self._view = QTableView()
        self._view.setModel(self._proxy)
        self._view.setSortingEnabled(True)
        self._view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._view.setDragEnabled(True)
        self._view.setAcceptDrops(True)
        self._view.setDropIndicatorShown(True)
        self._view.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self._view.setDragDropOverwriteMode(False)
        self._view.setShowGrid(False)
        self._view.setAlternatingRowColors(False)
        self._view.verticalHeader().setVisible(False)
        self._view.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch  # Title column stretches
        )
        self._view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._view.customContextMenuRequested.connect(self._on_context_menu)
        self._view.installEventFilter(self)
        self._view.selectionModel().selectionChanged.connect(self._on_selection_changed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self._filter)
        layout.addWidget(self._view)

    # ------------------------------------------------------------------ public

    def set_section(
        self,
        set_list: SetList | None,
        section: Section | None,
        tracks: dict[UUID, Track],
    ) -> None:
        self._set_list = set_list
        self._section = section
        self._tracks = tracks
        if section is not None:
            self._model.set_section(section.track_ids, tracks, section.color)
        else:
            self._model.clear()
        # Re-attach selection listener (model reset detaches it in some Qt versions)
        self._view.selectionModel().selectionChanged.connect(self._on_selection_changed)

    def add_track(self, track: Track) -> None:
        """Add *track* to the project's track store and to the current section."""
        if self._section is None:
            return
        self._tracks[track.id] = track
        self._section.add_track(track.id)
        self._model.set_section(self._section.track_ids, self._tracks, self._section.color)
        self.section_modified.emit()

    def selected_track_id(self) -> UUID | None:
        indexes = self._view.selectedIndexes()
        if not indexes:
            return None
        # Map proxy index → source index to get the correct model row.
        source_idx = self._proxy.mapToSource(indexes[0])
        return self._model.track_id_at(source_idx.row())

    def current_section_color(self) -> RekordboxColor | None:
        return self._section.color if self._section else None

    # ------------------------------------------------------------ event filter

    def eventFilter(self, source, event) -> bool:
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QKeyEvent

        if source is self._view and event.type() == QEvent.Type.KeyPress:
            if isinstance(event, QKeyEvent) and event.key() == Qt.Key.Key_Delete:
                self._remove_selected()
                return True
        return super().eventFilter(source, event)

    # ------------------------------------------------------------- slots

    def _on_selection_changed(self) -> None:
        self.track_selected.emit(self.selected_track_id())

    def _on_tracks_reordered(self, new_ids: list[UUID]) -> None:
        if self._section is not None:
            self._section.track_ids = new_ids
            self.section_modified.emit()

    def _on_context_menu(self, pos) -> None:
        menu = QMenu(self)
        tid = self.selected_track_id()
        if tid is not None:
            menu.addAction("Remove from section", self._remove_selected)
        menu.addSeparator()
        menu.addAction("Add track…", self.add_track_requested.emit)
        menu.exec(self._view.viewport().mapToGlobal(pos))

    def _remove_selected(self) -> None:
        tid = self.selected_track_id()
        if tid is None or self._section is None:
            return
        self.about_to_modify.emit()  # capture undo state before removal
        self._section.remove_track(tid)
        self._model.set_section(self._section.track_ids, self._tracks, self._section.color)
        self.section_modified.emit()
