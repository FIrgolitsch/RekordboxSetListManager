"""Dark QSS theme for Set Manager (Catppuccin Mocha-inspired)."""

from __future__ import annotations

# Palette:
#   base    #1e1e2e   mantle  #181825   crust   #11111b
#   surface #313244   overlay #45475a   muted   #585b70
#   text    #cdd6f4   subtext #a6adc8
#   blue    #89b4fa   lavender #b4befe
DARK_QSS = """
/* ── Base ──────────────────────────────────────────────────────────────── */
QMainWindow, QDialog, QWidget {
    background-color: #1e1e2e;
    color: #cdd6f4;
}

/* ── Menu bar ───────────────────────────────────────────────────────────── */
QMenuBar {
    background-color: #181825;
    color: #cdd6f4;
    border-bottom: 1px solid #313244;
}
QMenuBar::item {
    background-color: transparent;
    padding: 4px 10px;
}
QMenuBar::item:selected {
    background-color: #313244;
}

QMenu {
    background-color: #1e1e2e;
    color: #cdd6f4;
    border: 1px solid #313244;
}
QMenu::item {
    padding: 4px 20px;
}
QMenu::item:selected {
    background-color: #45475a;
}
QMenu::separator {
    height: 1px;
    background: #313244;
    margin: 3px 6px;
}

/* ── Status bar ─────────────────────────────────────────────────────────── */
QStatusBar {
    background-color: #181825;
    color: #a6adc8;
    border-top: 1px solid #313244;
}
QStatusBar QLabel {
    color: #a6adc8;
}

/* ── Splitter ───────────────────────────────────────────────────────────── */
QSplitter::handle {
    background-color: #313244;
}
QSplitter::handle:horizontal {
    width: 2px;
}
QSplitter::handle:vertical {
    height: 2px;
}

/* ── Buttons ────────────────────────────────────────────────────────────── */
QPushButton {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 4px;
    padding: 4px 14px;
}
QPushButton:hover {
    background-color: #45475a;
    border-color: #585b70;
}
QPushButton:pressed {
    background-color: #585b70;
}
QPushButton:disabled {
    color: #585b70;
    border-color: #313244;
}
QPushButton:default {
    border-color: #89b4fa;
}

/* ── Line edit ──────────────────────────────────────────────────────────── */
QLineEdit {
    background-color: #181825;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 3px;
    padding: 3px 6px;
    selection-background-color: #45475a;
}
QLineEdit:focus {
    border-color: #89b4fa;
}
QLineEdit:disabled {
    color: #585b70;
}

/* ── Text edit ──────────────────────────────────────────────────────────── */
QTextEdit, QPlainTextEdit {
    background-color: #181825;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 3px;
    selection-background-color: #45475a;
}
QTextEdit:focus, QPlainTextEdit:focus {
    border-color: #89b4fa;
}
QTextEdit:disabled, QPlainTextEdit:disabled {
    color: #585b70;
}

/* ── Combo box ──────────────────────────────────────────────────────────── */
QComboBox {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 3px;
    padding: 3px 6px;
}
QComboBox:hover {
    border-color: #585b70;
}
QComboBox::drop-down {
    border: none;
    width: 18px;
}
QComboBox QAbstractItemView {
    background-color: #1e1e2e;
    color: #cdd6f4;
    border: 1px solid #313244;
    selection-background-color: #45475a;
}

/* ── Table view ─────────────────────────────────────────────────────────── */
QTableView {
    background-color: #181825;
    color: #cdd6f4;
    gridline-color: #313244;
    border: 1px solid #313244;
    selection-background-color: #45475a;
    selection-color: #cdd6f4;
    alternate-background-color: #1e1e2e;
}
QTableView::item:selected {
    background-color: #45475a;
    color: #cdd6f4;
}

/* ── Tree / list view ───────────────────────────────────────────────────── */
QTreeWidget, QTreeView, QListView, QListWidget {
    background-color: #181825;
    color: #cdd6f4;
    border: 1px solid #313244;
    selection-background-color: #45475a;
    selection-color: #cdd6f4;
    alternate-background-color: #1e1e2e;
}
QTreeWidget::item:selected, QTreeView::item:selected,
QListView::item:selected, QListWidget::item:selected {
    background-color: #45475a;
    color: #cdd6f4;
}
QTreeWidget::item:hover, QTreeView::item:hover {
    background-color: #313244;
}
QTreeWidget::branch {
    background-color: #181825;
}

/* ── Header view ────────────────────────────────────────────────────────── */
QHeaderView {
    background-color: #181825;
}
QHeaderView::section {
    background-color: #181825;
    color: #a6adc8;
    border: none;
    border-right: 1px solid #313244;
    border-bottom: 1px solid #313244;
    padding: 4px 6px;
    font-weight: 500;
}
QHeaderView::section:hover {
    background-color: #313244;
    color: #cdd6f4;
}
QHeaderView::section:pressed {
    background-color: #45475a;
}
QHeaderView::section:first {
    border-left: none;
}

/* ── Scroll bars ────────────────────────────────────────────────────────── */
QScrollBar:vertical {
    background-color: #181825;
    width: 10px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background-color: #45475a;
    border-radius: 5px;
    min-height: 24px;
}
QScrollBar::handle:vertical:hover {
    background-color: #585b70;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }

QScrollBar:horizontal {
    background-color: #181825;
    height: 10px;
    margin: 0;
}
QScrollBar::handle:horizontal {
    background-color: #45475a;
    border-radius: 5px;
    min-width: 24px;
}
QScrollBar::handle:horizontal:hover {
    background-color: #585b70;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: none; }

/* ── Group box ──────────────────────────────────────────────────────────── */
QGroupBox {
    color: #a6adc8;
    border: 1px solid #45475a;
    border-radius: 4px;
    margin-top: 10px;
    padding-top: 8px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 4px;
    left: 10px;
    color: #a6adc8;
}

/* ── Tab widget ─────────────────────────────────────────────────────────── */
QTabWidget::pane {
    border: 1px solid #313244;
    background-color: #1e1e2e;
}
QTabBar::tab {
    background-color: #181825;
    color: #a6adc8;
    border: 1px solid #313244;
    border-bottom: none;
    padding: 5px 14px;
}
QTabBar::tab:selected {
    background-color: #1e1e2e;
    color: #cdd6f4;
}
QTabBar::tab:hover:!selected {
    background-color: #313244;
}

/* ── Spin box ───────────────────────────────────────────────────────────── */
QDoubleSpinBox, QSpinBox {
    background-color: #181825;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 3px;
    padding: 3px 6px;
}
QDoubleSpinBox:focus, QSpinBox:focus {
    border-color: #89b4fa;
}

/* ── Check box / radio button ───────────────────────────────────────────── */
QCheckBox, QRadioButton {
    color: #cdd6f4;
    spacing: 6px;
}
QCheckBox::indicator, QRadioButton::indicator {
    width: 14px;
    height: 14px;
    border: 1px solid #45475a;
    border-radius: 3px;
    background-color: #181825;
}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {
    border-color: #89b4fa;
}
QCheckBox::indicator:checked {
    background-color: #89b4fa;
    border-color: #89b4fa;
}
QRadioButton::indicator {
    border-radius: 7px;
}
QRadioButton::indicator:checked {
    background-color: #89b4fa;
    border-color: #89b4fa;
}

/* ── Dialog button box ────────────────────────────────────────────────── */
QDialogButtonBox QPushButton {
    min-width: 72px;
}

/* ── Tooltip ─────────────────────────────────────────────────────────────*/
QToolTip {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    padding: 3px;
}
"""


def apply_dark_theme(app) -> None:
    """Apply the built-in dark QSS theme to *app*."""
    app.setStyleSheet(DARK_QSS)
