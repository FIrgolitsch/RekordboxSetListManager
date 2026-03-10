"""Left panel: tree view of set lists and their sections."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QInputDialog,
    QMenu,
    QMessageBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from set_manager.models.enums import SectionType
from set_manager.models.project import Project
from set_manager.models.section import Section
from set_manager.models.set_list import SetList
from set_manager.utils.constants import SECTION_TYPE_LABELS

_ROLE_SET_LIST = Qt.ItemDataRole.UserRole
_ROLE_SECTION = Qt.ItemDataRole.UserRole + 1


class SectionPanel(QWidget):
    """Displays the SetList / Section hierarchy and emits selection signals."""

    # Emitted when the user selects a set list (section=None) or a section.
    selection_changed = Signal(object, object)  # (SetList | None, Section | None)
    # Emitted immediately before any structural change (for undo snapshot).
    about_to_modify = Signal()
    # Emitted after any structural change so MainWindow can mark the project dirty.
    project_changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._project: Project | None = None

        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_context_menu)
        self._tree.currentItemChanged.connect(self._on_item_changed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._tree)

    # ------------------------------------------------------------------ public

    def set_project(self, project: Project | None) -> None:
        self._project = project
        self.refresh()

    def refresh(self) -> None:
        """Repopulate the tree from the current project without changing selection."""
        # Remember which set list / section was selected.
        prev_sl_id = None
        prev_sec_id = None
        item = self._tree.currentItem()
        if item:
            sl = item.data(0, _ROLE_SET_LIST)
            sec = item.data(0, _ROLE_SECTION)
            if sl:
                prev_sl_id = sl.id
            if sec:
                prev_sec_id = sec.id

        self._tree.blockSignals(True)
        self._tree.clear()

        if self._project:
            for sl in self._project.set_lists:
                sl_item = self._make_sl_item(sl)
                self._tree.addTopLevelItem(sl_item)
                sl_item.setExpanded(True)
                for section in sl.sections:
                    sec_item = self._make_sec_item(sl, section)
                    sl_item.addChild(sec_item)
                    if prev_sec_id and section.id == prev_sec_id:
                        self._tree.setCurrentItem(sec_item)
                if prev_sl_id and sl.id == prev_sl_id and prev_sec_id is None:
                    self._tree.setCurrentItem(sl_item)

        self._tree.blockSignals(False)

    # ----------------------------------------------------------------- helpers

    def _make_sl_item(self, sl: SetList) -> QTreeWidgetItem:
        item = QTreeWidgetItem([sl.name])
        item.setData(0, _ROLE_SET_LIST, sl)
        item.setData(0, _ROLE_SECTION, None)
        return item

    def _make_sec_item(self, sl: SetList, section: Section) -> QTreeWidgetItem:
        label = SECTION_TYPE_LABELS.get(section.section_type, section.name)
        item = QTreeWidgetItem([f"{section.name}  ({label})"])
        item.setData(0, _ROLE_SET_LIST, sl)
        item.setData(0, _ROLE_SECTION, section)
        return item

    def _item_data(self, item: QTreeWidgetItem | None) -> tuple[SetList | None, Section | None]:
        if item is None:
            return None, None
        return item.data(0, _ROLE_SET_LIST), item.data(0, _ROLE_SECTION)

    # ------------------------------------------------------------- Qt signals

    def _on_item_changed(self, current, _previous) -> None:
        sl, sec = self._item_data(current)
        self.selection_changed.emit(sl, sec)

    def _on_context_menu(self, pos) -> None:
        if self._project is None:
            return
        item = self._tree.itemAt(pos)
        sl, sec = self._item_data(item)

        menu = QMenu(self)

        if sec is not None:
            # Right-clicked on a section
            menu.addAction("Rename Section", lambda: self._rename_section(sl, sec))
            theme_menu = menu.addMenu("Apply Theme")
            if self._project.themes:
                for theme in self._project.themes:
                    theme_menu.addAction(
                        theme.name, lambda t=theme: self._apply_theme(sl, t.name)
                    )
            else:
                act = theme_menu.addAction("(no themes defined)")
                act.setEnabled(False)
            menu.addSeparator()
            menu.addAction("Delete Section", lambda: self._delete_section(sl, sec))
        elif sl is not None:
            # Right-clicked on a set list
            menu.addAction("New Section", lambda: self._add_section(sl))
            menu.addAction("Rename Set List", lambda: self._rename_set_list(sl))
            menu.addSeparator()
            menu.addAction("Delete Set List", lambda: self._delete_set_list(sl))
            menu.addSeparator()

        menu.addAction("New Set List", self._add_set_list)
        menu.exec(self._tree.viewport().mapToGlobal(pos))

    # --------------------------------------------------------------- mutations

    def _add_set_list(self) -> None:
        name, ok = QInputDialog.getText(self, "New Set List", "Name:")
        if not ok or not name.strip():
            return
        self.about_to_modify.emit()
        sl = SetList(name=name.strip())
        self._project.add_set_list(sl)
        self.refresh()
        self.project_changed.emit()

    def _rename_set_list(self, sl: SetList) -> None:
        name, ok = QInputDialog.getText(self, "Rename Set List", "Name:", text=sl.name)
        if not ok or not name.strip():
            return
        self.about_to_modify.emit()
        sl.name = name.strip()
        self.refresh()
        self.project_changed.emit()

    def _delete_set_list(self, sl: SetList) -> None:
        answer = QMessageBox.question(
            self,
            "Delete Set List",
            f"Delete '{sl.name}' and all its sections?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.about_to_modify.emit()
        self._project.remove_set_list(sl.id)
        self.refresh()
        self.selection_changed.emit(None, None)
        self.project_changed.emit()

    def _add_section(self, sl: SetList) -> None:
        name, ok = QInputDialog.getText(self, "New Section", "Section name:")
        if not ok or not name.strip():
            return
        self.about_to_modify.emit()
        color = self._project.default_color_for(SectionType.GENERAL)
        section = Section(name=name.strip(), section_type=SectionType.GENERAL, color=color)
        sl.add_section(section)
        self.refresh()
        self.project_changed.emit()

    def _rename_section(self, sl: SetList, sec: Section) -> None:
        name, ok = QInputDialog.getText(self, "Rename Section", "Name:", text=sec.name)
        if not ok or not name.strip():
            return
        self.about_to_modify.emit()
        sec.name = name.strip()
        self.refresh()
        self.project_changed.emit()

    def _delete_section(self, sl: SetList, sec: Section) -> None:
        answer = QMessageBox.question(
            self,
            "Delete Section",
            f"Delete section '{sec.name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.about_to_modify.emit()
        sl.remove_section(sec.id)
        self.refresh()
        self.selection_changed.emit(sl, None)
        self.project_changed.emit()

    def _apply_theme(self, sl: SetList, theme_name: str) -> None:
        self.about_to_modify.emit()
        self._project.apply_theme_to_set_list(theme_name, sl.id)
        self.refresh()
        self.project_changed.emit()
