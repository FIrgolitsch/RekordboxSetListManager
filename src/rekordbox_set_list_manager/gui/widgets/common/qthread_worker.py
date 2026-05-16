"""Generic QThread worker for running callables off the main thread."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, Signal


class QThreadWorker(QObject):
    """Runs a callable in a background QThread.

    Usage::

        worker = QThreadWorker(lambda: do_something())
        thread = QThread(parent)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(on_done)
        worker.error.connect(on_error)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.start()
    """

    finished = Signal(object)  # emitted with the return value of the callable
    error = Signal(str)  # emitted with the error message on exception

    def __init__(self, fn: Callable[[], Any]) -> None:
        """Wrap *fn* for execution in a background QThread."""
        super().__init__()
        self._fn = fn

    def run(self) -> None:
        """Execute the callable in this thread and emit the result or error."""
        try:
            self.finished.emit(self._fn())
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))
