"""SectionBlock: collapsible section header + track table for the set list view."""

from __future__ import annotations

import subprocess
import sys
from typing import TYPE_CHECKING, cast
from uuid import UUID

if TYPE_CHECKING:
    from rekordbox_set_list_manager.controllers.edit_controller import EditController

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QVBoxLayout,
)

from rekordbox_set_list_manager.gui.delegates.indicator_delegate import IndicatorDelegate
from rekordbox_set_list_manager.gui.models.track_table_model import TrackTableModel
from rekordbox_set_list_manager.gui.widgets.common.track_table import TrackFilterProxyModel
from rekordbox_set_list_manager.gui.widgets.sections.section_table_view import (
    MIME_TYPE as _MIME_TYPE,
)
from rekordbox_set_list_manager.gui.widgets.sections.section_table_view import SectionTableView
from rekordbox_set_list_manager.models.enums import RekordboxColor
from rekordbox_set_list_manager.models.project import Project
from rekordbox_set_list_manager.models.section import Section
from rekordbox_set_list_manager.models.track import Track
from rekordbox_set_list_manager.utils.constants import REKORDBOX_COLOR_HEX, SECTION_TYPE_LABELS
from rekordbox_set_list_manager.utils.formatting import format_total_duration

_ROW_H = 24
_HDR_H = 30
_FALLBACK_HDR = "#3a3a5c"
# Default pixel widths for non-stretch columns: #, Artist, BPM, Key, Duration, Status
_COL_WIDTHS: dict[int, int] = {0: 30, 2: 140, 3: 55, 4: 80, 5: 65, 6: 60}


def _reveal_command(filepath: str) -> tuple[str, list[str]]:
    """Return (menu_label, subprocess_args) to reveal *filepath* in the system file manager."""
    from pathlib import Path  # noqa: PLC0415

    if sys.platform == "darwin":
        return "Reveal in Finder", ["/usr/bin/open", "-R", filepath]
    if sys.platform == "win32":
        return "Show in Explorer", ["explorer", f"/select,{filepath}"]
    # Linux / other: open the parent directory
    return "Open Containing Folder", ["xdg-open", str(Path(filepath).parent)]


def _header_hex(color: RekordboxColor | None) -> str:
    if color is None or color == RekordboxColor.NONE:
        return _FALLBACK_HDR
    c = QColor(REKORDBOX_COLOR_HEX.get(color, _FALLBACK_HDR))
    hsv = cast("tuple[float, float, float, float]", c.getHsvF())
    c.setHsvF(hsv[0], hsv[1], max(hsv[2] * 0.55, 0.25), hsv[3])
    return c.name()


# ─────────────────────────── Section block ────────────────────────────────────


class SectionBlock(QFrame):
    """A collapsible frame displaying a single set-list section with its tracks."""

    section_modified = Signal()
    track_selected = Signal(object)
    add_track_requested = Signal()
    add_from_rekordbox_requested = Signal()
    cross_section_drop = Signal(str, list, int, object)
    edit_requested = Signal()
    delete_requested = Signal()
    theme_requested = Signal(str)
    fix_match_requested = Signal(object)  # track_id
    add_to_section_requested = Signal(
        object, object, object
    )  # track_id, src_section_id, target_section_id
    collapsed_changed = Signal()

    def __init__(
        self,
        section: Section,
        tracks: dict[UUID, Track],
        project: Project,
        edit_ctrl: EditController,
        parent=None,
    ) -> None:
        """Build a collapsible block for *section* backed by *edit_ctrl*."""
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._section = section
        self._tracks = tracks
        self._project = project
        self._edit = edit_ctrl
        self._collapsed = False
        self._dup_ids: set[UUID] = set()

        self._toggle_btn = QPushButton("▼")
        self._toggle_btn.setFixedWidth(24)
        self._toggle_btn.setFlat(True)
        self._toggle_btn.setStyleSheet("color: white; font-size: 10px; padding: 0;")
        self._toggle_btn.setToolTip("Collapse / expand")
        self._toggle_btn.clicked.connect(self._on_toggle)

        self._hdr_label = QLabel()
        self._hdr_label.setStyleSheet("color: white; font-weight: bold;")
        self._type_badge = QLabel()
        self._type_badge.setStyleSheet("color: rgba(255,255,255,0.65); font-size: 11px;")

        self._hdr_frame = QFrame()
        self._hdr_frame.setFixedHeight(_HDR_H)
        self._hdr_frame.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._hdr_frame.customContextMenuRequested.connect(self._on_header_context_menu)
        self._hdr_frame.setAcceptDrops(True)

        hdr_row = QHBoxLayout(self._hdr_frame)
        hdr_row.setContentsMargins(4, 0, 8, 0)
        hdr_row.setSpacing(4)
        hdr_row.addWidget(self._toggle_btn)
        hdr_row.addWidget(self._hdr_label)
        hdr_row.addStretch()
        hdr_row.addWidget(self._type_badge)

        self._model = TrackTableModel()
        self._proxy = TrackFilterProxyModel()
        self._proxy.setSourceModel(self._model)
        # Filter changes resize the visible row set without going through
        # _sync_model — keep the fixed-height table in sync.
        self._proxy.rowsInserted.connect(self._update_view_height)
        self._proxy.rowsRemoved.connect(self._update_view_height)
        self._proxy.layoutChanged.connect(self._update_view_height)
        self._proxy.modelReset.connect(self._update_view_height)

        self._view = SectionTableView(section.id)
        self._view.setFrameShape(QFrame.Shape.NoFrame)
        self._view.setStyleSheet("QTableView { border: none; }")
        self._view.setModel(self._proxy)
        self._view.setItemDelegateForColumn(1, IndicatorDelegate(self._view))
        self._view.setItemDelegateForColumn(4, IndicatorDelegate(self._view))
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
        self._view.verticalHeader().setDefaultSectionSize(_ROW_H)
        self._view.horizontalHeader().setVisible(False)
        self._view.horizontalHeader().setMaximumHeight(0)
        self._view.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for col, w in _COL_WIDTHS.items():
            self._view.setColumnWidth(col, w)
        self._view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._view.customContextMenuRequested.connect(self._on_track_context_menu)
        self._view.installEventFilter(self)
        self._hdr_frame.installEventFilter(self)
        self._view.selectionModel().selectionChanged.connect(self._on_selection_changed)
        self._view.cross_section_drop.connect(
            lambda src, rows, dest: self.cross_section_drop.emit(src, rows, dest, self._section)
        )
        self._model.tracks_reordered.connect(self._on_tracks_reordered)

        self._empty_row = QLabel("No tracks yet — press Ctrl+T or right-click to add one.")
        self._empty_row.setStyleSheet("color: #a6adc8; padding: 4px 8px;")
        self._empty_row.setVisible(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._hdr_frame)
        layout.addWidget(self._empty_row)
        layout.addWidget(self._view)

        self._hdr_normal_css = ""
        self.refresh_appearance()
        self._sync_model()

    @property
    def section(self) -> Section:
        """Return the section represented by this block."""
        return self._section

    def set_filter_text(self, text: str) -> None:
        """Propagate the filter text to the proxy model."""
        self._proxy.set_filter_text(text)

    def update_tracks(self, tracks: dict[UUID, Track]) -> None:
        """Replace the track mapping and refresh the model."""
        self._tracks = tracks
        self._sync_model()

    def set_dup_ids(self, ids: set[UUID]) -> None:
        """Store the cross-section duplicate track IDs and refresh the model."""
        self._dup_ids = set(ids)
        self._sync_model()

    def filtered_row_count(self) -> int:
        """Return the number of rows currently visible through the filter proxy."""
        return self._proxy.rowCount()

    def refresh_model(self) -> None:
        """Re-sync the model from the current section and track data."""
        self._sync_model()

    def refresh_appearance(self) -> None:
        """Reapply header colour, border radius, and type badge from section data."""
        hdr_color = _header_hex(self._section.color)
        radius = "border-radius: 3px;" if self._collapsed else "border-radius: 3px 3px 0 0;"
        self._hdr_normal_css = f"background-color: {hdr_color}; {radius}"
        self._hdr_frame.setStyleSheet(self._hdr_normal_css)
        type_label = SECTION_TYPE_LABELS.get(
            self._section.section_type, str(self._section.section_type)
        )
        self._type_badge.setText(type_label)
        self._refresh_header_text()

    def selected_track_id(self) -> UUID | None:
        """Return the UUID of the currently selected track, or None."""
        indexes = self._view.selectedIndexes()
        if not indexes:
            return None
        src = self._proxy.mapToSource(indexes[0])
        return self._model.track_id_at(src.row())

    def clear_selection(self) -> None:
        """Clear the track selection in this block's table view."""
        self._view.clearSelection()

    def set_column_width(self, logical_index: int, width: int) -> None:
        """Set the width of a column by its logical index."""
        self._view.setColumnWidth(logical_index, width)

    def _sync_model(self) -> None:
        self._model.set_section(
            self._section.track_ids,
            self._tracks,
            self._section.color,
            self._section.id,
            dup_ids=self._dup_ids,
        )
        self._update_view_height()
        self._refresh_header_text()
        # Visibility is gated on the collapsed state so a refresh driven by
        # dup flags or track updates can never re-show a clipped table.
        has_tracks = bool(self._section.track_ids)
        self._empty_row.setVisible(not has_tracks and not self._collapsed)
        self._view.setVisible(has_tracks and not self._collapsed)

    def _refresh_header_text(self) -> None:
        n = len(self._section.track_ids)
        # Defensive .get(): a dangling track id contributes 0 rather than
        # raising KeyError, matching the model's lookup idiom.
        total = sum(
            track.duration or 0
            for tid in self._section.track_ids
            if (track := self._tracks.get(tid)) is not None
        )
        if total > 0:
            self._hdr_label.setText(f"{self._section.name}  ({n} · {format_total_duration(total)})")
        else:
            self._hdr_label.setText(f"{self._section.name}  ({n})")

    def _update_view_height(self) -> None:
        n = self._proxy.rowCount()
        self._view.setFixedHeight(n * _ROW_H + 4)
        self._view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def _on_toggle(self) -> None:
        self._collapsed = not self._collapsed
        if self._collapsed:
            self._view.setFixedHeight(0)
            self._view.setVisible(False)
            self._empty_row.setVisible(False)
            self.setFixedHeight(_HDR_H)
        else:
            self.setMinimumHeight(0)
            self.setMaximumHeight(16777215)
            self._sync_model()
        self._toggle_btn.setText("▶" if self._collapsed else "▼")
        self.refresh_appearance()
        self.collapsed_changed.emit()

    def _on_selection_changed(self) -> None:
        self.track_selected.emit(self.selected_track_id())

    def _on_tracks_reordered(self, new_ids: list[UUID]) -> None:
        self._edit.reorder_section_tracks(self._section.id, new_ids)
        self._refresh_header_text()
        self.section_modified.emit()

    def _on_track_context_menu(self, pos) -> None:
        menu = QMenu(self)
        tid = self.selected_track_id()
        if tid is not None:
            menu.addAction("Remove from section", self._remove_selected)
            menu.addAction(
                "Fix Match…",
                lambda: self.fix_match_requested.emit(self.selected_track_id()),
            )
            # "Reveal in file manager" if the track has a local file path
            track = self._project.get_track(tid) if tid is not None else None
            if track is not None and track.filepath:
                label, cmd = _reveal_command(track.filepath)
                menu.addAction(label, lambda c=cmd: subprocess.run(c, check=False))  # noqa: S603
            # "Add to section" submenu — list all sections except current
            other_sections = [s for s in self._project.sections if s.id != self._section.id]
            if other_sections:
                add_menu = menu.addMenu("Add to section")
                for sec in other_sections:
                    add_menu.addAction(
                        sec.name,
                        lambda checked=False, sid=sec.id: self.add_to_section_requested.emit(
                            tid, self._section.id, sid
                        ),
                    )
        menu.addSeparator()
        menu.addAction("Add track…", self.add_track_requested.emit)
        menu.addAction("Add from Rekordbox DB…", self.add_from_rekordbox_requested.emit)
        menu.exec(self._view.viewport().mapToGlobal(pos))

    def _on_header_context_menu(self, pos) -> None:
        menu = QMenu(self)
        menu.addAction("Edit Section…", self.edit_requested.emit)
        if self._project.themes:
            theme_menu = menu.addMenu("Apply Theme")
            for theme in self._project.themes:
                theme_menu.addAction(
                    theme.name,
                    lambda checked=False, n=theme.name: self.theme_requested.emit(n),
                )
        menu.addSeparator()
        menu.addAction("Delete Section", self.delete_requested.emit)
        menu.exec(self._hdr_frame.mapToGlobal(pos))

    def _remove_selected(self) -> None:
        tid = self.selected_track_id()
        if tid is None:
            return
        self._edit.remove_track(tid, self._section.id)
        self._sync_model()
        self.section_modified.emit()

    def eventFilter(self, source, event) -> bool:
        """Handle Delete key for track removal and header drag-and-drop events."""
        if (
            source is self._view
            and event.type() == QEvent.Type.KeyPress
            and isinstance(event, QKeyEvent)
            and event.key() == Qt.Key.Key_Delete
        ):
            self._remove_selected()
            return True
        if source is self._hdr_frame:
            if event.type() in (
                QEvent.Type.DragEnter,
                QEvent.Type.DragMove,
            ) and event.mimeData().hasFormat(_MIME_TYPE):
                if event.type() == QEvent.Type.DragEnter:
                    self._hdr_frame.setStyleSheet(
                        self._hdr_normal_css + " border-bottom: 2px solid rgba(255,255,255,0.8);"
                    )
                event.acceptProposedAction()
                return True
            if event.type() == QEvent.Type.DragLeave:
                self._hdr_frame.setStyleSheet(self._hdr_normal_css)
                return False
            if event.type() == QEvent.Type.Drop and event.mimeData().hasFormat(_MIME_TYPE):
                self._hdr_frame.setStyleSheet(self._hdr_normal_css)
                self._handle_header_drop(event)
                event.acceptProposedAction()
                return True
        return super().eventFilter(source, event)

    def _handle_header_drop(self, event) -> None:
        raw = bytes(event.mimeData().data(_MIME_TYPE)).decode()
        if ":" in raw:
            src_id, rows_str = raw.split(":", 1)
            rows = [int(r) for r in rows_str.split(",") if r.strip().isdigit()]
        else:
            src_id = str(self._section.id)
            rows = [int(r) for r in raw.split(",") if r.strip().isdigit()]
        if not rows:
            return
        if src_id == str(self._section.id):
            ids = self._section.track_ids
            moving = [ids[r] for r in sorted(rows) if 0 <= r < len(ids)]
            src_set = set(rows)
            remaining = [tid for i, tid in enumerate(ids) if i not in src_set]
            self._edit.reorder_section_tracks(self._section.id, remaining + moving)
            self._sync_model()
            self.section_modified.emit()
        else:
            self.cross_section_drop.emit(src_id, rows, len(self._section.track_ids), self._section)
