"""Smoke tests for ImportDialog and the streaming service tab contract."""

from __future__ import annotations

import pytest
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QListWidgetItem, QMessageBox, QTabWidget

from rekordbox_set_list_manager.gui.widgets.common.busy_dialog import BusyDialog
from rekordbox_set_list_manager.gui.widgets.dialogs.import_dialog import ImportDialog
from rekordbox_set_list_manager.gui.widgets.streaming.service_tab import StreamingServiceTab
from rekordbox_set_list_manager.models.project import Project
from rekordbox_set_list_manager.models.track import Track
from rekordbox_set_list_manager.services.skipped_item import SkippedItem
from rekordbox_set_list_manager.services.streaming_base import PlaylistResult


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


# ---------------------------------------------------------------------------
# tracks_loaded signal — (tracks, skipped items, playlist_id)
# ---------------------------------------------------------------------------


class _StubService:
    """Duck-typed streaming service returning a fixed PlaylistResult."""

    def __init__(self, result: PlaylistResult) -> None:
        self._result = result

    def get_playlist_tracks(self, playlist_id: str) -> PlaylistResult:
        return self._result


def _run_synchronously(self, fn):
    return True, fn(), None


def test_service_tab_emits_tracks_and_skipped_items(qtbot, monkeypatch):
    """tracks_loaded carries (list[Track], list[SkippedItem], playlist_id)."""
    result = PlaylistResult(
        tracks=[Track(title="Song", artist="Artist")],
        skipped=[
            SkippedItem(label="Podcast Episode", reason="not a track (episode or empty slot)")
        ],
    )
    tab = StreamingServiceTab(_StubService(result))
    qtbot.addWidget(tab)
    monkeypatch.setattr(BusyDialog, "run", _run_synchronously)
    tab._playlist_ids = ["pl-1"]
    item = QListWidgetItem("Playlist")
    tab._playlist_widget.addItem(item)

    spy = QSignalSpy(tab.tracks_loaded)
    tab._on_playlist_clicked(item)

    assert spy.count() == 1
    tracks, skipped, playlist_id = spy.at(0)
    assert [t.title for t in tracks] == ["Song"]
    assert [s.label for s in skipped] == ["Podcast Episode"]
    assert playlist_id == "pl-1"


# ---------------------------------------------------------------------------
# Skipped-item details surface
# ---------------------------------------------------------------------------


def test_import_dialog_itemizes_skipped_items(qtbot, project, monkeypatch):
    """Skipped fetch entries drive the count label and the details popup."""
    dialog = ImportDialog(project)
    qtbot.addWidget(dialog)
    dialog._playlist_tracks = [Track(title="Song", artist="Artist")]
    dialog._skipped_items = [
        SkippedItem(label="(empty playlist slot)", reason="not a track (episode or empty slot)"),
        SkippedItem(label="id-9", reason="no track name (unavailable)"),
    ]

    dialog._on_tracks_loaded()

    assert not dialog._details_btn.isHidden()
    assert "2 skipped" in dialog._match_label.text()

    captured = {}

    def _capture_exec(box):
        captured["detail"] = box.detailedText()
        return QMessageBox.DialogCode.Accepted

    monkeypatch.setattr(QMessageBox, "exec", _capture_exec)
    dialog._show_skipped_details()

    assert "(empty playlist slot) — not a track" in captured["detail"]
    assert "id-9 — no track name" in captured["detail"]


def test_import_dialog_hides_details_button_without_skips(qtbot, project):
    dialog = ImportDialog(project)
    qtbot.addWidget(dialog)
    dialog._playlist_tracks = [Track(title="Song", artist="Artist")]

    dialog._on_tracks_loaded()

    assert dialog._details_btn.isHidden()
    assert dialog._match_label.text() == "1 track(s) loaded."


class _StubMatcher:
    """Matcher stand-in reporting a fixed isrc_duplicates list."""

    def __init__(self, duplicates: list[SkippedItem]) -> None:
        self.isrc_duplicates = list(duplicates)

    def match(self, tracks, collection):
        return []


def test_on_match_merges_isrc_duplicates_into_details(qtbot, project):
    """Duplicate-ISRC groups reported by the matcher appear in the details surface."""
    dialog = ImportDialog(project)
    qtbot.addWidget(dialog)
    dialog._playlist_tracks = [Track(title="Song", artist="Artist")]
    dialog._collection_browser._collection = [Track(title="Song", artist="Artist", rekordbox_id=1)]
    dialog._matcher = _StubMatcher(
        [SkippedItem(label="USX1", reason="3 tracks share this ISRC; lowest Rekordbox ID used")]
    )

    dialog._on_match()

    assert not dialog._details_btn.isHidden()
    detail = dialog._skipped_details_text()
    assert "USX1" in detail
    assert "lowest Rekordbox ID" in detail
    assert "duplicate ISRC" in dialog._match_label.text()
