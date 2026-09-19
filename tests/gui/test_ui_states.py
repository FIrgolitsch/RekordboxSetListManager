"""UI state tests: indicator payloads, Camelot display, and panel consistency.

Asserts model-level ``UserRole`` payloads and display strings — never pixels
(the delegate that paints from these payloads lands separately).
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt

from rekordbox_set_list_manager.gui.delegates.indicator_delegate import IndicatorDelegate
from rekordbox_set_list_manager.gui.main_window import MainWindow
from rekordbox_set_list_manager.gui.models.track_table_model import TrackTableModel
from rekordbox_set_list_manager.models.enums import KeyRelation
from rekordbox_set_list_manager.models.section import Section
from rekordbox_set_list_manager.models.track import Track


@pytest.fixture
def window(qtbot, monkeypatch):
    # Prevent the "Unsaved changes?" popup on close (qtbot tears down by closing)
    monkeypatch.setattr(
        "rekordbox_set_list_manager.controllers.file_controller.FileController.confirm_discard",
        lambda self: True,
    )
    w = MainWindow()
    qtbot.addWidget(w)
    return w


def _make_model(tracks: list[Track], dup_ids=None) -> TrackTableModel:
    """Return a TrackTableModel populated with *tracks* in list order."""
    section = Section(name="Peak")
    model = TrackTableModel()
    model.set_section(
        [t.id for t in tracks],
        {t.id: t for t in tracks},
        section.color,
        section.id,
        dup_ids=dup_ids,
    )
    return model


# ------------------------------------------------------------ key column


def test_key_column_shows_rekordbox_plus_camelot(qapp):
    t1 = Track(title="Sundown", artist="DJ Koze", key="Am")
    t2 = Track(title="Atlas", artist="Bicep", key="Dm")
    model = _make_model([t1, t2])

    assert model.data(model.index(0, 4), Qt.ItemDataRole.DisplayRole) == "Am (8A)"
    assert model.data(model.index(1, 4), Qt.ItemDataRole.DisplayRole) == "Dm (7A)"


def test_key_column_missing_key_shows_dash(qapp):
    model = _make_model([Track(title="X", artist="Y")])

    assert model.data(model.index(0, 4), Qt.ItemDataRole.DisplayRole) == "—"


# ------------------------------------------------------- compat payloads


def test_compat_payload_is_transition_into_row(qapp):
    t1 = Track(title="Sundown", artist="DJ Koze", key="Am")  # 8A
    t2 = Track(title="Atlas", artist="Bicep", key="Dm")  # 7A
    model = _make_model([t1, t2])

    # Row 0 has no predecessor — no transition to grade.
    assert model.data(model.index(0, 4), Qt.ItemDataRole.UserRole) is None
    # 8A -> 7A is one fifth down on the Camelot wheel (delta 11).
    assert model.data(model.index(1, 4), Qt.ItemDataRole.UserRole) == KeyRelation.FIFTH_DOWN


def test_compat_payload_none_for_unparseable_predecessor(qapp):
    t1 = Track(title="Mystery", artist="X", key="not a key")
    t2 = Track(title="Atlas", artist="Bicep", key="Dm")
    model = _make_model([t1, t2])

    # Unparseable predecessor -> no payload -> no badge (never a false clash).
    assert model.data(model.index(1, 4), Qt.ItemDataRole.UserRole) is None


def test_compat_payload_none_for_clashing_keys(qapp):
    t1 = Track(title="Sundown", artist="DJ Koze", key="Am")  # 8A
    t2 = Track(title="Atlas", artist="Bicep", key="Fm")  # 4A — clash vs 8A
    model = _make_model([t1, t2])

    assert model.data(model.index(1, 4), Qt.ItemDataRole.UserRole) is None


# ----------------------------------------------------------- dup payloads


def test_dup_flag_on_title_column(qapp):
    t1 = Track(title="Sundown", artist="DJ Koze")
    t2 = Track(title="Atlas", artist="Bicep")
    model = _make_model([t1, t2], dup_ids={t1.id})

    assert model.data(model.index(0, 1), Qt.ItemDataRole.UserRole)
    assert model.data(model.index(1, 1), Qt.ItemDataRole.UserRole) is None


def test_no_dup_flag_without_dup_ids(qapp):
    t1 = Track(title="Sundown", artist="DJ Koze")
    model = _make_model([t1])

    assert model.data(model.index(0, 1), Qt.ItemDataRole.UserRole) is None


# ------------------------------------------------------ match info panel


def _seed_blocks(window, track: Track, section: Section):
    """Attach *track*/*section* to the project and rebuild the view blocks."""
    window._project.add_track(track)
    window._project.add_section(section)
    window._ctrl.project_changed.emit(window._project)
    return window._multi_section_view._blocks[str(section.id)]


def test_match_info_key_row_matches_column_format(window, track, section):
    window._project.add_track(track)
    window._project.add_section(section)

    window._multi_section_view.track_selected.emit(track.id, section)

    assert window._match_info._vals["Key"].text() == "Am (8A)"


# ----------------------------------------------------- delegate install


def test_indicator_delegate_installed_on_key_and_title_columns(window, track, section):
    block = _seed_blocks(window, track, section)

    assert isinstance(block._view.itemDelegateForColumn(4), IndicatorDelegate)
    assert isinstance(block._view.itemDelegateForColumn(1), IndicatorDelegate)


# ------------------------------------------------------- section header


def test_section_header_shows_count_and_duration(window, track, section):
    block = _seed_blocks(window, track, section)

    # track fixture has duration=390 -> 6:30
    assert block._hdr_label.text() == "Peak  (1 · 6:30)"


def test_section_header_omits_duration_when_all_unknown(window):
    t = Track(title="X", artist="Y")  # duration=None
    sec = Section(name="Peak")
    sec.add_track(t.id)
    window._project.add_track(t)
    window._project.add_section(sec)
    window._ctrl.project_changed.emit(window._project)
    block = window._multi_section_view._blocks[str(sec.id)]

    assert block._hdr_label.text() == "Peak  (1)"
    assert "·" not in block._hdr_label.text()


# --------------------------------------------------------- empty section


def test_empty_section_shows_inline_guidance_row(window):
    sec = Section(name="Peak")
    window._project.add_section(sec)
    window._ctrl.project_changed.emit(window._project)
    block = window._multi_section_view._blocks[str(sec.id)]

    assert not block._empty_row.isHidden()
    assert block._view.isHidden()
    assert "No tracks yet" in block._empty_row.text()


def test_collapsed_section_stays_hidden_across_dup_refresh(window, track, section):
    block = _seed_blocks(window, track, section)

    block._on_toggle()  # collapse
    assert block._view.isHidden()

    block.set_dup_ids({track.id})  # refresh must not re-show a clipped table
    assert block._view.isHidden()

    block._on_toggle()  # re-expand
    assert not block._view.isHidden()
