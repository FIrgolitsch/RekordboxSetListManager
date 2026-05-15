"""Application bootstrap."""

from __future__ import annotations

import contextlib
import datetime
import sys
import traceback
from typing import TYPE_CHECKING

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMessageBox

from rekordbox_set_list_manager import __version__
from rekordbox_set_list_manager.gui.main_window import MainWindow
from rekordbox_set_list_manager.services import telemetry
from rekordbox_set_list_manager.services.autosave import CRASH_LOG
from rekordbox_set_list_manager.utils.theme import apply_dark_theme

if TYPE_CHECKING:
    from types import TracebackType


def _install_exception_hook() -> None:
    _orig = sys.excepthook

    def _hook(
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_tb: TracebackType | None,
    ) -> None:
        log_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        with contextlib.suppress(Exception):
            CRASH_LOG.parent.mkdir(parents=True, exist_ok=True)
            with CRASH_LOG.open("a", encoding="utf-8") as fh:
                fh.write(f"\n--- {datetime.datetime.now(datetime.UTC).isoformat()} ---\n")
                fh.write(log_text)
        with contextlib.suppress(Exception):
            QMessageBox.critical(
                None,
                "Unexpected Error",
                f"An unexpected error occurred.\n\nDetails saved to:\n{CRASH_LOG}",
            )
        _orig(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook


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


def main() -> None:
    if "--version" in sys.argv or "-V" in sys.argv:
        print(f"Set Manager {__version__}")  # noqa: T201
        sys.exit(0)

    _install_exception_hook()
    app = QApplication(sys.argv)
    app.setApplicationName("Set Manager")
    app.setApplicationVersion(__version__)

    apply_dark_theme(app)

    icon = _make_icon()
    app.setWindowIcon(icon)

    window = MainWindow()
    window.setWindowIcon(icon)
    window.show()

    telemetry.record("app_start", version=__version__)

    try:
        sys.exit(app.exec())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        QMessageBox.critical(
            None,
            "Fatal Error",
            f"An unexpected error occurred and Set Manager must close:\n\n{exc}",
        )
        sys.exit(1)
