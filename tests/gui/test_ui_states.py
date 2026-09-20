"""UI state tests: indicator payloads, Camelot display, and panel consistency.

Asserts model-level ``UserRole`` payloads and display strings — never pixels
(the delegate that paints from these payloads lands separately).
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QDialog, QMessageBox

from rekordbox_set_list_manager.gui.delegates.indicator_delegate import IndicatorDelegate
from rekordbox_set_list_manager.gui.main_window import MainWindow
from rekordbox_set_list_manager.gui.models.track_table_model import TrackTableModel
from rekordbox_set_list_manager.gui.widgets.dialogs.add_track_dialog import (
    AddTrackDialog,
)
from rekordbox_set_list_manager.models.enums import KeyRelation
from rekordbox_set_list_manager.models.section import Section
from rekordbox_set_list_manager.models.track import Track
from rekordbox_set_list_manager.utils.theme import apply_dark_theme


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
    assert block._hdr_label.text() == "Peak (1 · 6:30)"


def test_section_header_omits_duration_when_all_unknown(window):
    t = Track(title="X", artist="Y")  # duration=None
    sec = Section(name="Peak")
    sec.add_track(t.id)
    window._project.add_track(t)
    window._project.add_section(sec)
    window._ctrl.project_changed.emit(window._project)
    block = window._multi_section_view._blocks[str(sec.id)]

    assert block._hdr_label.text() == "Peak (1)"
    assert "·" not in block._hdr_label.text()


def test_section_header_elides_name_keeps_count_suffix(window):
    """A long section name elides with '…' while '({n} · {M:SS})' stays intact."""
    t = Track(title="X", artist="Y", duration=390)
    sec = Section(name="A" * 80)
    sec.add_track(t.id)
    window._project.add_track(t)
    window._project.add_section(sec)
    window._ctrl.project_changed.emit(window._project)
    block = window._multi_section_view._blocks[str(sec.id)]

    block._hdr_frame.resize(260, 30)
    block._apply_header_text()

    text = block._hdr_label.text()
    assert text.endswith(" (1 · 6:30)")
    assert "…" in text


# --------------------------------------------------------- empty section


def test_empty_section_shows_inline_guidance_row(window):
    sec = Section(name="Peak")
    window._project.add_section(sec)
    window._ctrl.project_changed.emit(window._project)
    block = window._multi_section_view._blocks[str(sec.id)]

    assert not block._empty_row.isHidden()
    assert block._view.isHidden()
    assert "No tracks yet" in block._empty_row.text()


def test_empty_row_context_menu_offers_add_track(window):
    """The 'right-click to add one' hint must be a real affordance."""
    sec = Section(name="Peak")
    window._project.add_section(sec)
    window._ctrl.project_changed.emit(window._project)
    block = window._multi_section_view._blocks[str(sec.id)]

    assert block._empty_row.contextMenuPolicy() == Qt.ContextMenuPolicy.CustomContextMenu
    labels = [a.text() for a in block._build_empty_menu().actions()]
    assert "Add &Track…" in labels
    assert "Add from Rekordbox &DB…" in labels


def test_header_context_menu_offers_add_track(window, track, section):
    block = _seed_blocks(window, track, section)

    labels = [a.text() for a in block._build_header_menu().actions()]
    assert "Add &Track…" in labels
    assert "Add from Rekordbox &DB…" in labels


def test_activated_block_becomes_current_section(window):
    """Clicking a trackless section's chrome aims Ctrl+T at that section."""
    sec1 = Section(name="Peak")
    sec2 = Section(name="Empty")
    window._project.add_section(sec1)
    window._project.add_section(sec2)
    window._ctrl.project_changed.emit(window._project)
    view = window._multi_section_view

    view._blocks[str(sec2.id)].activated.emit()

    assert view.current_section() is sec2


def test_activating_block_clears_previous_selection(window, track, section):
    """Activation moves the visual focus — never two highlighted rows."""
    sec2 = Section(name="Empty")
    window._project.add_track(track)
    window._project.add_section(section)
    window._project.add_section(sec2)
    window._ctrl.project_changed.emit(window._project)
    view = window._multi_section_view
    block1 = view._blocks[str(section.id)]
    _select_first_row(block1)

    view._blocks[str(sec2.id)].activated.emit()

    assert block1.selected_track_id() is None
    assert view.current_section() is sec2


def test_collapsed_section_stays_hidden_across_dup_refresh(window, track, section):
    block = _seed_blocks(window, track, section)

    block._on_toggle()  # collapse
    assert block._view.isHidden()

    block.set_dup_ids({track.id})  # refresh must not re-show a clipped table
    assert block._view.isHidden()

    block._on_toggle()  # re-expand
    assert not block._view.isHidden()


# -------------------------------------------------------- filter banner


def test_filter_no_results_banner_shows_and_hides(window, track, section):
    _seed_blocks(window, track, section)
    view = window._multi_section_view

    assert view._filter_empty_banner.isHidden()

    view._filter.setText("zzz")
    assert not view._filter_empty_banner.isHidden()
    assert view._filter_empty_headline.text() == 'No tracks match "zzz"'

    view._filter.setText("")
    assert view._filter_empty_banner.isHidden()

    view._filter.setText(track.title[:4])  # "Sund" matches "Sundown"
    assert view._filter_empty_banner.isHidden()


def test_filter_placeholder_shows_shortcut_hint(window):
    assert window._multi_section_view._filter.placeholderText() == ("Filter tracks… (Ctrl+F)")


def test_filter_banner_retracks_membership_changes(window, track, section, monkeypatch):
    """The no-results banner follows membership changes, not just filter text."""
    _seed_blocks(window, track, section)  # "Sundown" — does not match "zzz"
    view = window._multi_section_view
    view._filter.setText("zzz")
    assert not view._filter_empty_banner.isHidden()

    # The Ctrl+T path: dialog → funnel mutation → refresh_section.
    matching = Track(title="zzz anthem", artist="X")
    monkeypatch.setattr(AddTrackDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(AddTrackDialog, "track", lambda self: matching)
    window._track_ctrl.add_track(section)

    assert view._filter_empty_banner.isHidden(), (
        "a matching row is visible but the banner stayed up"
    )

    # Removing the last matching row must bring the banner back.
    block = view._blocks[str(section.id)]
    _select_first_row(block)  # only the matching row survives the filter
    block._remove_selected()

    assert not view._filter_empty_banner.isHidden(), (
        "filtered view is empty but the banner stayed hidden"
    )


# ------------------------------------------------------ dup refresh (view)


def test_dup_flags_refresh_on_membership_changes(window, track, section, monkeypatch):
    section2 = Section(name="Build")
    window._project.add_track(track)
    window._project.add_section(section)
    window._project.add_section(section2)
    window._ctrl.project_changed.emit(window._project)
    view = window._multi_section_view
    block1 = view._blocks[str(section.id)]
    block2 = view._blocks[str(section2.id)]

    assert track.id not in block1._model._dup_ids

    # A direct controller call fires the recompute via the
    # project_changed wire — no manual refresh needed.
    window._edit_ctrl.add_track(track, section2.id)
    assert track.id in block1._model._dup_ids
    assert track.id in block2._model._dup_ids

    window._edit_ctrl.remove_track(track.id, section2.id)
    assert track.id not in block1._model._dup_ids
    assert track.id not in block2._model._dup_ids

    # Re-add, then delete the second section — the delete path emits only
    # the view-level project_changed, and the surviving block's flags must
    # still clear.
    window._edit_ctrl.add_track(track, section2.id)
    assert track.id in block1._model._dup_ids
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    block2.delete_requested.emit()
    assert track.id not in block1._model._dup_ids


def test_single_section_track_never_flagged_as_dup(window, track, section):
    block = _seed_blocks(window, track, section)

    assert track.id not in block._model._dup_ids
    assert block._model.data(block._model.index(0, 1), Qt.ItemDataRole.UserRole) is None


def _select_first_row(block) -> None:
    idx = block._proxy.index(0, 0)
    block._view.selectionModel().select(
        idx,
        QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows,
    )


def test_unchanged_dup_refresh_keeps_table_selection(window, track, section):
    """A mutation that leaves dup flags unchanged must not reset the model —
    otherwise every note keystroke clears the selection and wipes the editor."""
    block = _seed_blocks(window, track, section)
    _select_first_row(block)
    assert block.selected_track_id() == track.id
    assert window._transition_note._edit.isEnabled()

    # A note edit fires project_changed -> _refresh_dup_ids with the same set.
    window._edit_ctrl.set_transition_note(section.id, track.id, "smooth")

    assert block.selected_track_id() == track.id
    assert window._transition_note._edit.isEnabled()


def test_changed_dup_refresh_keeps_table_selection(window, track, section):
    """A real dup-flag change repaints via dataChanged — selection survives."""
    section2 = Section(name="Build")
    window._project.add_track(track)
    window._project.add_section(section)
    window._project.add_section(section2)
    window._ctrl.project_changed.emit(window._project)
    view = window._multi_section_view
    block1 = view._blocks[str(section.id)]
    _select_first_row(block1)

    window._edit_ctrl.add_track(track, section2.id)  # now a cross-section dup

    assert track.id in block1._model._dup_ids
    assert block1.selected_track_id() == track.id


def test_indicator_tooltips_explain_markers(qapp):
    t1 = Track(title="Sundown", artist="DJ Koze", key="Am")
    t2 = Track(title="Atlas", artist="Bicep", key="Dm")
    model = _make_model([t1, t2], dup_ids={t1.id})

    assert model.data(model.index(1, 4), Qt.ItemDataRole.ToolTipRole)  # transition tier
    assert model.data(model.index(0, 1), Qt.ItemDataRole.ToolTipRole)  # dup marker
    assert model.data(model.index(0, 6), Qt.ItemDataRole.ToolTipRole)  # status glyph
    assert model.data(model.index(0, 4), Qt.ItemDataRole.ToolTipRole) is None  # row 0
    assert model.data(model.index(1, 1), Qt.ItemDataRole.ToolTipRole) is None  # no dup


def test_new_menu_actions_have_mnemonics(window):
    for act in (
        window._act_add_track,
        window._act_add_section,
        window._act_filter,
        window._act_export_setlist,
    ):
        assert "&" in act.text()


# --------------------------------------------------- indicator rendering


def _color_close(c: QColor, hex_str: str, tol: int = 40) -> bool:
    ref = QColor(hex_str)
    return (
        abs(c.red() - ref.red()) <= tol
        and abs(c.green() - ref.green()) <= tol
        and abs(c.blue() - ref.blue()) <= tol
    )


def _is_text_pixel(c: QColor) -> bool:
    # Catppuccin text (#cdd6f4): light and faintly blue.  The indicator
    # fills (green/amber/lavender) all fail at least one bound.
    return c.red() >= 185 and c.green() >= 185 and c.blue() >= 200


def _leftmost_text_x(img, rect) -> int | None:
    for x in range(rect.left(), rect.right()):
        for y in range(rect.top(), rect.bottom()):
            if _is_text_pixel(img.pixelColor(x, y)):
                return x
    return None


def test_indicator_dots_render_legibly_on_dark_theme(window, qapp):
    """Painted output check: tier colors, dup marker, +16px text offset."""
    t1 = Track(title="Sundown", artist="DJ Koze", key="Dm")  # 7A
    t2 = Track(title="Atlas", artist="Bicep", key="Am")  # 8A — FIFTH_UP (green)
    t3 = Track(title="Anthem", artist="X", key="Bm")  # 10A — ENERGY_UP (amber)
    sec = Section(name="Peak")
    for t in (t1, t2, t3):
        sec.add_track(t.id)
        window._project.add_track(t)
    sec2 = Section(name="Build")
    sec2.add_track(t1.id)  # cross-section duplicate → lavender marker on t1
    window._project.add_section(sec)
    window._project.add_section(sec2)
    window._ctrl.project_changed.emit(window._project)
    block = window._multi_section_view._blocks[str(sec.id)]

    old_qss = qapp.styleSheet()
    apply_dark_theme(qapp)
    try:
        window.show()
        qapp.processEvents()
        img = block._view.viewport().grab().toImage()

        def dot_probe(row: int, col: int):
            rect = block._view.visualRect(block._proxy.index(row, col))
            return rect, img.pixelColor(rect.left() + 9, rect.center().y())

        _, green = dot_probe(1, 4)  # Dm -> Am is a fifth up
        _, amber = dot_probe(2, 4)  # Am -> Bm is an energy move
        _, dup = dot_probe(0, 1)  # t1 sits in two sections
        _, none_dot = dot_probe(0, 4)  # row 0 has no predecessor — no badge

        assert _color_close(green, "#a6e3a1"), f"safe tier dot: {green.name()}"
        assert _color_close(amber, "#f9e2af"), f"energy tier dot: {amber.name()}"
        assert _color_close(dup, "#b4befe"), f"dup marker: {dup.name()}"
        for hex_str in ("#a6e3a1", "#f9e2af", "#b4befe"):
            assert not _color_close(none_dot, hex_str), (
                f"phantom marker on badgeless row: {none_dot.name()}"
            )

        # Selected rows keep the dot and the QSS selection chrome.
        block._view.selectRow(1)
        qapp.processEvents()
        sel_img = block._view.viewport().grab().toImage()
        sel_rect = block._view.visualRect(block._proxy.index(1, 4))
        sel_dot = sel_img.pixelColor(sel_rect.left() + 9, sel_rect.center().y())
        sel_bg = sel_img.pixelColor(sel_rect.right() - 8, sel_rect.center().y())
        assert _color_close(sel_dot, "#a6e3a1"), f"selected dot: {sel_dot.name()}"
        assert _color_close(sel_bg, "#45475a"), f"selection chrome: {sel_bg.name()}"
        block._view.clearSelection()

        # The dup marker pushes its cell text right by _TEXT_OFFSET.
        dup_rect = block._view.visualRect(block._proxy.index(0, 1))
        plain_rect = block._view.visualRect(block._proxy.index(1, 1))
        dup_x = _leftmost_text_x(img, dup_rect)
        plain_x = _leftmost_text_x(img, plain_rect)
        assert dup_x is not None
        assert plain_x is not None
        assert 12 <= dup_x - plain_x <= 20
    finally:
        qapp.setStyleSheet(old_qss)
