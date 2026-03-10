"""Application bootstrap."""

import sys

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from set_manager.gui.main_window import MainWindow
from set_manager.utils.theme import apply_dark_theme


def _make_icon() -> QIcon:
    """Return a simple programmatic 'SM' icon for the application."""
    size = 64
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Rounded blue background
    painter.setBrush(QColor("#89b4fa"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(QRect(0, 0, size, size), 12, 12)

    # "SM" text
    font = QFont("Arial", 22, QFont.Weight.Bold)
    painter.setFont(font)
    painter.setPen(QColor("#1e1e2e"))
    painter.drawText(QRect(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, "SM")

    painter.end()
    return QIcon(pix)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Set Manager")
    app.setApplicationVersion("0.1.0")

    apply_dark_theme(app)

    icon = _make_icon()
    app.setWindowIcon(icon)

    window = MainWindow()
    window.setWindowIcon(icon)
    window.show()

    try:
        sys.exit(app.exec())
    except Exception as exc:  # noqa: BLE001
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.critical(
            None,
            "Fatal Error",
            f"An unexpected error occurred and Set Manager must close:\n\n{exc}",
        )
        sys.exit(1)
