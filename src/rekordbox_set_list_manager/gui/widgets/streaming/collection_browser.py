"""Shared widget: browse a Rekordbox collection with load/filter/select."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from rekordbox_set_list_manager.gui.widgets.common.busy_dialog import BusyDialog
from rekordbox_set_list_manager.services import collection_cache
from rekordbox_set_list_manager.services.rekordbox_collection import (
    db_loader,
    xml_loader,
)
from rekordbox_set_list_manager.services.rekordbox_db import RekordboxDbService

if TYPE_CHECKING:
    from rekordbox_set_list_manager.models.track import Track


class CollectionBrowserWidget(QWidget):
    """Load-buttons + filter + table for browsing a Rekordbox track collection.

    Signals:
        collection_loaded(int): Emitted after a successful load with the track count.
        track_selected(object): Emitted when the table selection changes; passes the selected :class:`~rekordbox_set_list_manager.models.track.Track` or ``None``.
    """

    collection_loaded = Signal(int)
    track_selected = Signal(object)  # Track | None

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialise an empty Rekordbox collection browser widget."""
        super().__init__(parent)
        self._collection: list[Track] = []
        self._selected: Track | None = None

        # ── Load buttons ───────────────────────────────────────────────────
        load_row = QHBoxLayout()
        self._btn_xml = QPushButton("Load from Rekordbox XML…")
        self._btn_xml.clicked.connect(self._load_xml)
        self._btn_db = QPushButton("Auto-detect Rekordbox DB")
        self._btn_db.clicked.connect(self._load_db)
        load_row.addWidget(self._btn_xml)
        load_row.addWidget(self._btn_db)
        load_row.addStretch()

        # ── Filter ─────────────────────────────────────────────────────────
        self._filter = QLineEdit()
        self._filter.setPlaceholderText("Filter collection…")
        self._filter.setClearButtonEnabled(True)
        self._filter.textChanged.connect(self._populate_table)

        # ── Table ──────────────────────────────────────────────────────────
        self._table = QTableWidget()
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels(["Title", "Artist", "BPM", "Key", "File"])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSortingEnabled(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setShowGrid(False)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)

        # ── Layout ─────────────────────────────────────────────────────────
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(load_row)
        layout.addWidget(self._filter)
        layout.addWidget(self._table, 1)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def collection(self) -> list[Track]:
        """Currently loaded track list (empty until a source is loaded)."""
        return self._collection

    @property
    def selected_track(self) -> Track | None:
        """Currently selected track, or ``None`` if nothing is selected."""
        return self._selected

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _load_xml(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Rekordbox XML", "", "Rekordbox XML (*.xml)"
        )
        if not path:
            return
        self._load(xml_loader(Path(path)))

    def _load_db(self) -> None:
        # Fast path: serve from cache if DB file hasn't changed.
        db_path = RekordboxDbService().get_db_path()
        if db_path:
            cached = collection_cache.load_if_valid(db_path)
            if cached is not None:
                self._collection = cached
                self._populate_table()
                self.collection_loaded.emit(len(self._collection))
                return
        # Slow path: load from DB asynchronously, then save to cache.
        if self._load(db_loader()) and db_path:
            collection_cache.save(self._collection, db_path)

    def _load(self, loader) -> bool:  # type: ignore[type-arg]  # loader: CollectionLoader
        """Async load via BusyDialog; returns ``True`` on success."""
        self._btn_xml.setEnabled(False)
        self._btn_db.setEnabled(False)
        try:
            dlg = BusyDialog("Loading collection…", self, cancellable=True)
            ok, result, error = dlg.run(loader.load)
        finally:
            self._btn_xml.setEnabled(True)
            self._btn_db.setEnabled(True)
        if not ok:
            if error:
                QMessageBox.warning(self, "Load failed", error)
            return False
        tracks: list[Track] = result  # type: ignore[assignment]
        self._collection = tracks
        self._populate_table()
        self.collection_loaded.emit(len(self._collection))
        return True

    def _populate_table(self) -> None:
        self._table.setSortingEnabled(False)
        self._table.setRowCount(0)
        filter_text = self._filter.text().lower()
        for track in self._collection:
            if filter_text and not self._matches_filter(track, filter_text):
                continue
            row = self._table.rowCount()
            self._table.insertRow(row)
            file_name = Path(track.filepath).name if track.filepath else "—"
            cells = [
                track.title,
                track.artist,
                f"{track.bpm:.1f}" if track.bpm is not None else "—",
                track.key or "—",
                file_name,
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, track)
                self._table.setItem(row, col, item)
        self._table.setSortingEnabled(True)
        self._table.resizeColumnToContents(0)
        self._table.resizeColumnToContents(1)

    def _matches_filter(self, track: Track, text: str) -> bool:
        return (
            text in track.title.lower()
            or text in track.artist.lower()
            or bool(track.filepath and text in track.filepath.lower())
        )

    def _on_selection_changed(self) -> None:
        items = self._table.selectedItems()
        if items:
            self._selected = items[0].data(Qt.ItemDataRole.UserRole)
        else:
            self._selected = None
        self.track_selected.emit(self._selected)
