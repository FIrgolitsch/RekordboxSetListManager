"""Smoke tests for ImportDialog."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QTabWidget

from rekordbox_set_list_manager.gui.widgets.dialogs.import_dialog import ImportDialog
from rekordbox_set_list_manager.models.project import Project


@pytest.fixture
def project():
    return Project(name="Smoke Test")


@pytest.fixture(autouse=True)
def _no_auto_connect(monkeypatch):
    """Prevent auto-connecting to streaming services (avoids network calls and BusyDialog)."""
    monkeypatch.setattr(ImportDialog, "_try_auto_connect", lambda self: None)


def test_import_dialog_opens_without_error(qtbot, project):
    dialog = ImportDialog(project)
    qtbot.addWidget(dialog)
    assert dialog is not None


def test_import_dialog_has_service_tabs(qtbot, project):
    dialog = ImportDialog(project)
    qtbot.addWidget(dialog)

    tabs = dialog.findChild(QTabWidget)

    assert tabs is not None
    assert tabs.count() >= 2


def test_import_dialog_tab_switch_does_not_crash(qtbot, project):
    dialog = ImportDialog(project)
    qtbot.addWidget(dialog)

    tabs = dialog.findChild(QTabWidget)
    assert tabs is not None

    for i in range(tabs.count()):
        tabs.setCurrentIndex(i)
    # Reaching here without exception is the assertion
