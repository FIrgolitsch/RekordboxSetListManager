"""Multi-section view — all sections of a project shown simultaneously."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from rekordbox_set_list_manager.controllers.edit_controller import EditController

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from rekordbox_set_list_manager.gui.models.track_table_model import TrackTableModel
from rekordbox_set_list_manager.gui.widgets.common.color_picker import (
    ColorPickerWidget,  # noqa: F401 — re-exported
)
from rekordbox_set_list_manager.gui.widgets.common.track_table import TrackFilterProxyModel
from rekordbox_set_list_manager.gui.widgets.dialogs.section_edit_dialog import SectionEditDialog
from rekordbox_set_list_manager.gui.widgets.sections.section_block import _COL_WIDTHS, SectionBlock
from rekordbox_set_list_manager.models.project import Project
from rekordbox_set_list_manager.models.section import Section
from rekordbox_set_list_manager.models.track import Track

_TABLE_HDR_H = 24


class MultiSectionView(QWidget):
    """Shows all sections of the project stacked vertically.

    A project is treated as a single set list with multiple sections.
    If a project contains more than one set list (e.g. loaded from an old
    file), only the first is shown; future saves will retain all of them.

    Signals
    -------
    about_to_modify   — before any mutation (MainWindow pushes undo snapshot)
    project_changed   — structural change (section added / renamed / deleted)
    section_modified  — track content change
    track_selected    — (UUID | None, Section | None)
    add_track_requested — Section
    """

    about_to_modify = Signal()
    project_changed = Signal()
    import_requested = Signal()  # user clicked Import from Streaming Service
    section_modified = Signal()
    track_selected = Signal(object, object)
    add_track_requested = Signal(object)
    fix_match_requested = Signal(object, object)  # track_id, section

    def __init__(self, edit_ctrl: EditController, parent=None) -> None:
        """Initialise the multi-section view with *edit_ctrl*."""
        super().__init__(parent)
        self._project: Project | None = None
        self._edit = edit_ctrl
        self._blocks: dict[str, SectionBlock] = {}
        self._active_block: SectionBlock | None = None

        # ── Toolbar: filter + add section ────────────────────────────────
        self._filter = QLineEdit()
        self._filter.setPlaceholderText("Filter tracks…")
        self._filter.setClearButtonEnabled(True)
        self._filter.textChanged.connect(self._on_filter_changed)

        self._btn_add_sec = QPushButton("+ Section")
        self._btn_add_sec.setToolTip("Add a new section")
        self._btn_add_sec.setEnabled(False)
        self._btn_add_sec.clicked.connect(self._on_add_section)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(4, 2, 4, 2)
        toolbar.setSpacing(6)
        toolbar.addWidget(self._filter)
        toolbar.addWidget(self._btn_add_sec)

        # ── Scroll area ──────────────────────────────────────────────────
        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 4, 0, 4)
        self._content_layout.setSpacing(8)
        self._content_layout.addStretch()

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setWidget(self._content)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)

        # ── Shared column header (one header for all section tables) ─────
        self._header_model = TrackTableModel()
        self._header_proxy = TrackFilterProxyModel()
        self._header_proxy.setSourceModel(self._header_model)
        self._shared_header = QHeaderView(Qt.Orientation.Horizontal)
        self._shared_header.setModel(self._header_proxy)
        self._shared_header.setFixedHeight(_TABLE_HDR_H)
        self._shared_header.setStretchLastSection(False)
        self._shared_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._shared_header.setHighlightSections(False)
        self._shared_header.setSectionsClickable(False)
        self._shared_header.sectionResized.connect(self._on_header_section_resized)
        for col, w in _COL_WIDTHS.items():
            self._shared_header.resizeSection(col, w)

        # Header row: header + stretch so it stays left-aligned when narrower than full width
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(0)
        header_row.addWidget(self._shared_header)
        header_row.addStretch()

        self._scroll.viewport().installEventFilter(self)

        # ── Empty-state banner (shown when project has 0 sections) ────────
        self._empty_banner = self._build_empty_banner()
        self._content_layout.insertWidget(0, self._empty_banner)
        self._empty_banner.hide()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addLayout(toolbar)
        root.addLayout(header_row)
        root.addWidget(self._scroll)

    # ─────────────────────────── public API ───────────────────────────────

    def set_project(self, project: Project | None) -> None:
        """Load *project* into the view and rebuild all section blocks."""
        self._project = project
        self._btn_add_sec.setEnabled(project is not None)
        self._rebuild_blocks()

    def focus_filter(self) -> None:
        """Focus the filter field and select all text."""
        self._filter.setFocus()
        self._filter.selectAll()

    def add_section_interactive(self) -> None:
        """Programmatically trigger the 'Add Section' button (for keyboard shortcut)."""
        if self._btn_add_sec.isEnabled():
            self._on_add_section()

    def refresh(self) -> None:
        """Rebuild all blocks (e.g. after import added a new section)."""
        self._rebuild_blocks()

    def refresh_tracks(self, tracks: dict[UUID, Track]) -> None:
        """Push an updated track mapping to all section blocks."""
        for block in self._blocks.values():
            block.update_tracks(tracks)

    def refresh_section(self, section: Section) -> None:
        """Reload the model for a single section block."""
        block = self._blocks.get(str(section.id))
        if block:
            block.refresh_model()

    def add_track_to_section(self, track: Track, section: Section) -> None:
        """Add *track* to *section* and refresh the corresponding block."""
        if self._project:
            self._project.tracks[track.id] = track
        section.add_track(track.id)
        self.refresh_section(section)
        self.section_modified.emit()

    def current_section(self) -> Section | None:
        """Return the section whose block is currently active, or the first section."""
        if self._active_block is not None:
            return self._active_block.section
        if self._project and self._project.sections:
            block = self._blocks.get(str(self._project.sections[0].id))
            return block.section if block else None
        return None

    def scroll_to_section(self, section_id: UUID) -> None:
        """Scroll the viewport to ensure the section block is visible."""
        block = self._blocks.get(str(section_id))
        if block:
            self._scroll.ensureWidgetVisible(block)

    # ─────────────────────────── private ──────────────────────────────────

    def _build_empty_banner(self) -> QFrame:
        banner = QFrame()
        banner.setFrameShape(QFrame.Shape.StyledPanel)
        banner.setObjectName("empty_banner")

        headline = QLabel("No sections yet")
        headline.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = headline.font()
        font.setPointSize(font.pointSize() + 3)
        font.setBold(True)
        headline.setFont(font)

        body = QLabel("Add a section manually, or import a set list from Spotify or Tidal.")
        body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body.setWordWrap(True)

        btn_add = QPushButton("+ Add Section")
        btn_add.clicked.connect(self._on_add_section)
        btn_import = QPushButton("Import from Streaming Service…")
        btn_import.clicked.connect(self.import_requested.emit)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.addStretch()
        btn_row.addWidget(btn_add)
        btn_row.addWidget(btn_import)
        btn_row.addStretch()

        layout = QVBoxLayout(banner)
        layout.setContentsMargins(24, 32, 24, 32)
        layout.setSpacing(12)
        layout.addWidget(headline)
        layout.addWidget(body)
        layout.addLayout(btn_row)
        return banner

    def _rebuild_blocks(self) -> None:
        self._active_block = None
        self._blocks.clear()
        self.track_selected.emit(None, None)
        # index 0 = banner, last item = trailing stretch → keep both
        _KEEP_ITEMS = 2  # index 0 = banner, last item = trailing stretch
        while self._content_layout.count() > _KEEP_ITEMS:
            item = self._content_layout.takeAt(1)
            if item is not None and item.widget() is not None:
                widget = item.widget()
                assert widget is not None  # noqa: S101
                widget.deleteLater()
        if self._project is None:
            self._empty_banner.hide()
            return
        if not self._project.sections:
            self._empty_banner.show()
            return
        self._empty_banner.hide()
        for i, section in enumerate(self._project.sections):
            block = self._make_block(section)
            self._content_layout.insertWidget(i + 1, block)

    def _make_block(self, section: Section) -> SectionBlock:
        assert self._project is not None  # noqa: S101
        block = SectionBlock(section, self._project.tracks, self._project, self._edit)
        self._blocks[str(section.id)] = block
        block.section_modified.connect(self.section_modified)
        block.add_track_requested.connect(lambda sec=section: self.add_track_requested.emit(sec))
        block.track_selected.connect(lambda tid, b=block: self._on_block_track_selected(tid, b))
        block.cross_section_drop.connect(self._on_cross_section_drop)
        block.edit_requested.connect(lambda b=block: self._on_edit_section(b))
        block.delete_requested.connect(lambda b=block: self._on_delete_section(b))
        block.theme_requested.connect(self._on_apply_theme)
        block.fix_match_requested.connect(
            lambda tid, sec=section: self.fix_match_requested.emit(tid, sec)
        )
        block.add_to_section_requested.connect(self._on_add_to_section)
        block.collapsed_changed.connect(self._content.adjustSize)
        if self._filter.text():
            block.set_filter_text(self._filter.text())
        for col in range(self._shared_header.count()):
            if self._shared_header.sectionResizeMode(col) != QHeaderView.ResizeMode.Stretch:
                block.set_column_width(col, self._shared_header.sectionSize(col))
        return block

    # ──────────────── section management ──────────────────────────────────

    def _on_add_to_section(
        self, track_id: UUID, src_section_id: UUID, target_section_id: UUID
    ) -> None:
        """Move *track_id* from *src_section_id* to *target_section_id*."""
        if self._project is None or src_section_id == target_section_id:
            return
        if self._project.get_track(track_id) is None:
            return
        src_block = self._blocks.get(str(src_section_id))
        target_block = self._blocks.get(str(target_section_id))
        if src_block is None or target_block is None:
            return
        dest_idx = len(target_block.section.track_ids)
        self._edit.move_track(track_id, src_section_id, target_section_id, dest_idx)
        src_block.refresh_model()
        target_block.refresh_model()
        self.section_modified.emit()

    def _on_add_section(self) -> None:
        if self._project is None:
            return
        dialog = SectionEditDialog(self, "New Section")
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name = dialog.section_name()
        if not name:
            return
        st = dialog.section_type()
        color = self._project.default_color_for(st)
        section = Section(name=name, section_type=st, color=color)
        self._edit.add_section(section)
        block = self._make_block(section)
        self._empty_banner.hide()
        self._content_layout.insertWidget(self._content_layout.count() - 1, block)
        QTimer.singleShot(0, lambda b=block: self._scroll.ensureWidgetVisible(b))
        self.project_changed.emit()

    def _on_edit_section(self, block: SectionBlock) -> None:
        sec = block.section
        dialog = SectionEditDialog(
            self,
            "Edit Section",
            current_name=sec.name,
            current_type=sec.section_type,
            current_color=sec.color,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name = dialog.section_name()
        if not name:
            return
        self._edit.edit_section(sec.id, name, dialog.section_type(), dialog.section_color())
        block.refresh_appearance()
        self.project_changed.emit()

    def _on_delete_section(self, block: SectionBlock) -> None:
        if self._project is None:
            return
        sec = block.section
        answer = QMessageBox.question(
            self,
            "Delete Section",
            f"Delete section '{sec.name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        del self._blocks[str(sec.id)]
        self._edit.remove_section(sec.id)
        self._content_layout.removeWidget(block)
        block.deleteLater()
        if self._active_block is block:
            self._active_block = None
        self.track_selected.emit(None, None)
        self.project_changed.emit()

    def _on_apply_theme(self, theme_name: str) -> None:
        if self._project is None:
            return
        self._edit.apply_theme(theme_name)
        for block in self._blocks.values():
            block.refresh_appearance()
        self.project_changed.emit()

    # ──────────────── selection / filter / cross-section DnD ──────────────

    def _on_block_track_selected(self, track_id, block: SectionBlock) -> None:
        if track_id is not None and self._active_block is not block:
            if self._active_block is not None:
                self._active_block.clear_selection()
            self._active_block = block
        elif track_id is None and self._active_block is block:
            self._active_block = None
        self.track_selected.emit(track_id, block.section if track_id else None)

    def _on_filter_changed(self, text: str) -> None:
        for block in self._blocks.values():
            block.set_filter_text(text)

    def resizeEvent(self, event) -> None:
        """Sync the shared header width on resize."""
        super().resizeEvent(event)
        self._sync_header_width()

    def eventFilter(self, obj, event) -> bool:
        """Sync the shared header width when the scroll viewport is resized."""
        if obj is self._scroll.viewport() and event.type() == QEvent.Type.Resize:
            self._sync_header_width()
        return super().eventFilter(obj, event)

    def _sync_header_width(self) -> None:
        """Set shared header width to match the scroll viewport (handles scrollbar space)."""
        vp_w = self._scroll.viewport().width()
        if vp_w > 0:
            self._shared_header.setFixedWidth(vp_w)

    def _on_header_section_resized(self, logical_index: int, _old: int, new_size: int) -> None:
        for block in self._blocks.values():
            block.set_column_width(logical_index, new_size)

    def _on_cross_section_drop(
        self,
        src_id_str: str,
        rows: list[int],
        dest_row: int,
        dest_section: Section,
    ) -> None:
        src_block = self._blocks.get(src_id_str)
        if src_block is None:
            return
        src_section = src_block.section
        valid_rows = sorted(r for r in rows if 0 <= r < len(src_section.track_ids))
        if not valid_rows:
            return
        moving_ids = [src_section.track_ids[r] for r in valid_rows]
        actual_dest = max(0, min(dest_row, len(dest_section.track_ids)))
        self._edit.move_tracks_batch(moving_ids, src_section.id, dest_section.id, actual_dest)
        src_block.refresh_model()
        dest_block = self._blocks.get(str(dest_section.id))
        if dest_block:
            dest_block.refresh_model()
        self.section_modified.emit()
