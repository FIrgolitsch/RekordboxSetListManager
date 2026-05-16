"""Dialog for creating and editing section name themes."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from rekordbox_set_list_manager.models.enums import SectionType
from rekordbox_set_list_manager.models.project import Project
from rekordbox_set_list_manager.models.section_name_theme import SectionNameTheme
from rekordbox_set_list_manager.utils.constants import SECTION_TYPE_LABELS


class ThemeDialog(QDialog):
    """Manage section name themes stored in the project.

    Left side lists the themes; right side shows and edits the name mappings
    for the selected theme.
    """

    def __init__(self, project: Project, parent=None) -> None:
        """Initialise the theme management dialog for *project*."""
        super().__init__(parent)
        self.setWindowTitle("Section Name Themes")
        self.setMinimumSize(560, 400)
        self._project = project

        # ---- Left: theme list ----
        self._theme_list = QListWidget()
        self._theme_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._theme_list.currentRowChanged.connect(self._on_theme_selected)

        add_btn = QPushButton("New Theme")
        add_btn.clicked.connect(self._add_theme)
        self._del_btn = QPushButton("Delete")
        self._del_btn.clicked.connect(self._delete_theme)

        list_btns = QHBoxLayout()
        list_btns.addWidget(add_btn)
        list_btns.addWidget(self._del_btn)

        left = QVBoxLayout()
        left.addWidget(QLabel("Themes"))
        left.addWidget(self._theme_list, 1)
        left.addLayout(list_btns)

        # ---- Right: name mapping editor ----
        self._mapping_fields: dict[SectionType, QLineEdit] = {}
        mapping_layout = QVBoxLayout()
        for st in SectionType:
            label = SECTION_TYPE_LABELS.get(st, st.value)
            row = QHBoxLayout()
            row.addWidget(QLabel(label), 1)
            field = QLineEdit()
            field.setPlaceholderText(f"e.g. {label}")
            field.editingFinished.connect(self._save_current_mapping)
            self._mapping_fields[st] = field
            row.addWidget(field, 2)
            mapping_layout.addLayout(row)

        mapping_box = QGroupBox("Section Name Overrides")
        mapping_box.setLayout(mapping_layout)

        right = QVBoxLayout()
        right.addWidget(mapping_box, 1)

        # ---- Combine ----
        body = QHBoxLayout()
        body.addLayout(left, 1)
        body.addLayout(right, 2)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.accept)

        root = QVBoxLayout(self)
        root.addLayout(body)
        root.addWidget(buttons)

        self._refresh_theme_list()
        self._set_mapping_enabled(False)

    # ----------------------------------------------------------------- helpers

    def _refresh_theme_list(self) -> None:
        self._theme_list.blockSignals(True)
        self._theme_list.clear()
        for theme in self._project.themes:
            self._theme_list.addItem(theme.name)
        self._theme_list.blockSignals(False)
        self._del_btn.setEnabled(len(self._project.themes) > 1)

    def _current_theme(self) -> SectionNameTheme | None:
        row = self._theme_list.currentRow()
        if row < 0 or row >= len(self._project.themes):
            return None
        return self._project.themes[row]

    def _set_mapping_enabled(self, enabled: bool) -> None:
        for field in self._mapping_fields.values():
            field.setEnabled(enabled)
            if not enabled:
                field.clear()

    def _load_theme_mapping(self, theme: SectionNameTheme) -> None:
        for st, field in self._mapping_fields.items():
            field.setText(theme.names.get(st, ""))
        self._set_mapping_enabled(True)

    def _save_current_mapping(self) -> None:
        theme = self._current_theme()
        if theme is None:
            return
        theme.names = {
            st: field.text().strip()
            for st, field in self._mapping_fields.items()
            if field.text().strip()
        }

    # ------------------------------------------------------------------- slots

    def _on_theme_selected(self, row: int) -> None:
        if row < 0 or row >= len(self._project.themes):
            self._set_mapping_enabled(False)
            return
        self._load_theme_mapping(self._project.themes[row])

    def _add_theme(self) -> None:
        name, ok = QInputDialog.getText(self, "New Theme", "Theme name:")
        if not ok or not name.strip():
            return
        if self._project.get_theme(name.strip()) is not None:
            QMessageBox.warning(self, "Theme exists", f"A theme named '{name}' already exists.")
            return
        theme = SectionNameTheme(name=name.strip())
        self._project.add_theme(theme)
        self._refresh_theme_list()
        self._theme_list.setCurrentRow(len(self._project.themes) - 1)

    def _delete_theme(self) -> None:
        theme = self._current_theme()
        if theme is None:
            return
        if len(self._project.themes) <= 1:
            QMessageBox.information(
                self,
                "Cannot Delete",
                "At least one theme must remain.",
            )
            return
        answer = QMessageBox.question(
            self,
            "Delete Theme",
            f"Delete theme '{theme.name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._project.remove_theme(theme.name)
        self._refresh_theme_list()
        self._set_mapping_enabled(False)
