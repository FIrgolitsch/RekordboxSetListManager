"""Dialog for creating and editing a project section."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
)

from rekordbox_set_list_manager.gui.widgets.common.color_picker import ColorPickerWidget
from rekordbox_set_list_manager.models.enums import RekordboxColor, SectionType
from rekordbox_set_list_manager.utils.constants import SECTION_TYPE_LABELS


class SectionEditDialog(QDialog):
    """Modal dialog for creating or editing a section's name, type, and color."""

    def __init__(
        self,
        parent=None,
        title: str = "New Section",
        current_name: str = "",
        current_type: SectionType = SectionType.GENERAL,
        current_color: RekordboxColor | None = None,
    ) -> None:
        """Initialise the section edit dialog with optional pre-filled values."""
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(320)

        self._type_combo = QComboBox()
        for st in SectionType:
            self._type_combo.addItem(SECTION_TYPE_LABELS.get(st, str(st)), st)
            if st == current_type:
                self._type_combo.setCurrentIndex(self._type_combo.count() - 1)
        self._type_combo.currentIndexChanged.connect(self._on_type_changed)

        # Name field — only shown for General (custom) sections.
        self._name_edit = QLineEdit(current_name if current_type == SectionType.GENERAL else "")
        self._name_edit.setPlaceholderText("Section name")
        self._name_label = QLabel("Name:")
        is_general = current_type == SectionType.GENERAL
        self._name_label.setVisible(is_general)
        self._name_edit.setVisible(is_general)

        self._color_picker = ColorPickerWidget(selected=current_color)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QFormLayout(self)
        layout.addRow("Type:", self._type_combo)
        layout.addRow(self._name_label, self._name_edit)
        layout.addRow("Color:", self._color_picker)
        layout.addRow(buttons)

    def _on_type_changed(self) -> None:
        is_general = self._type_combo.currentData() == SectionType.GENERAL
        self._name_label.setVisible(is_general)
        self._name_edit.setVisible(is_general)
        self.adjustSize()

    def section_name(self) -> str:
        """Return the section name entered or derived from the selected type."""
        st = self._type_combo.currentData()
        if st == SectionType.GENERAL:
            return self._name_edit.text().strip()
        return SECTION_TYPE_LABELS.get(st, str(st))

    def section_type(self) -> SectionType:
        """Return the section type selected in the combo box."""
        return self._type_combo.currentData()

    def section_color(self) -> RekordboxColor | None:
        """Return the color selected in the color picker, or None."""
        return self._color_picker.selected_color()
