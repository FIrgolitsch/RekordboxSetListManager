"""Smoke tests for SettingsDialog Tidal auth wiring."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QThread

from rekordbox_set_list_manager.gui.widgets.dialogs.settings_dialog import SettingsDialog


@pytest.fixture(autouse=True)
def _no_auto_auth(monkeypatch):
    """Skip the silent Tidal auth attempt on dialog open (no network)."""
    monkeypatch.setattr(SettingsDialog, "_start_tidal_auth", lambda self, silent: None)


def test_settings_dialog_opens(qtbot):
    dialog = SettingsDialog()
    qtbot.addWidget(dialog)
    assert dialog._tidal_connect_btn is not None


def test_stop_tidal_auth_after_thread_finished_does_not_crash(qtbot):
    """Regression: refs to a finished+deleteLater'd QThread must be cleared."""
    dialog = SettingsDialog()
    qtbot.addWidget(dialog)
    dialog._tidal_thread = QThread(dialog)
    dialog._tidal_worker = MagicMock()

    dialog._clear_tidal_auth_refs()  # what the finished signal does

    dialog._stop_tidal_auth()  # must not raise on stale refs
    dialog.done(0)  # dialog close must not raise either
    assert dialog._tidal_thread is None
    assert dialog._tidal_worker is None


def test_stop_tidal_auth_cancels_running_thread(qtbot):
    dialog = SettingsDialog()
    qtbot.addWidget(dialog)
    worker = MagicMock()
    thread = QThread(dialog)
    thread.start()
    dialog._tidal_worker = worker
    dialog._tidal_thread = thread

    dialog._stop_tidal_auth()

    worker.cancel.assert_called_once()
    assert not thread.isRunning()
