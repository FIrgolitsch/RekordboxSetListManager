"""Mutation-contract capture tests for the EditController funnel.

Every user-reachable mutation routed through ``EditController`` must satisfy a
four-part contract:

1. the model reaches the expected end-state,
2. exactly one undo snapshot is pushed,
3. the project is marked dirty,
4. exactly one ``project_changed`` signal is emitted.

Assertions bind to model state and signals only — never widget internals.
Two known bypass sites (the view-level ``add_track_to_section`` and the theme
dialog path) are marked expected-change: they assert the intended funnel
contract so they turn green once the bypasses are migrated onto it.
"""

from __future__ import annotations

import pytest
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QDialog

from rekordbox_set_list_manager.gui.main_window import MainWindow
from rekordbox_set_list_manager.gui.widgets.dialogs.section_edit_dialog import (
    SectionEditDialog,
)
from rekordbox_set_list_manager.gui.widgets.dialogs.theme_dialog import ThemeDialog
from rekordbox_set_list_manager.models.enums import (
    MatchStatus,
    RekordboxColor,
    SectionType,
)
from rekordbox_set_list_manager.models.section import Section
from rekordbox_set_list_manager.models.section_name_theme import SectionNameTheme
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


# --------------------------------------------------------------------------- helpers


def _observe_mutation(window, mutate):
    """Run *mutate* and return ``(undo_depth_before, project_changed spy)``."""
    undo_before = len(window._edit_ctrl._stack._undo)
    spy = QSignalSpy(window._edit_ctrl.project_changed)
    mutate()
    return undo_before, spy


def _assert_funnel_contract(window, undo_before, spy) -> None:
    """Assert the undo / dirty / emission parts of the mutation contract."""
    assert len(window._edit_ctrl._stack._undo) - undo_before == 1
    assert window._ctrl.dirty is True
    assert spy.count() == 1


def _run_contract(window, mutate) -> None:
    """Run *mutate* and assert the undo / dirty / emission contract parts."""
    undo_before, spy = _observe_mutation(window, mutate)
    _assert_funnel_contract(window, undo_before, spy)


def _seed(window, *sections, tracks=()) -> None:
    """Populate the project model directly (setup only — bypasses the funnel)."""
    for track in tracks:
        window._project.add_track(track)
    for section in sections:
        window._project.add_section(section)


# --------------------------------------------------------------------------- sections


def test_add_section_contract(window):
    section = Section(name="Opening", section_type=SectionType.OPENER)

    _run_contract(window, lambda: window._edit_ctrl.add_section(section))

    assert window._project.sections == [section]


def test_remove_section_contract(window, section):
    _seed(window, section)

    _run_contract(window, lambda: window._edit_ctrl.remove_section(section.id))

    assert window._project.sections == []


def test_rename_section_contract(window, section):
    _seed(window, section)

    _run_contract(window, lambda: window._edit_ctrl.rename_section(section.id, "Sunset"))

    assert section.name == "Sunset"


def test_move_section_contract(window):
    first = Section(name="A", section_type=SectionType.GENERAL)
    second = Section(name="B", section_type=SectionType.GENERAL)
    _seed(window, first, second)

    _run_contract(window, lambda: window._edit_ctrl.move_section(second.id, 0))

    assert window._project.sections == [second, first]


def test_edit_section_contract(window, monkeypatch, section):
    _seed(window, section)
    # Rebuild the view so a block exists for the section under edit.
    window._ctrl.project_changed.emit(window._project)
    block = window._multi_section_view._blocks[str(section.id)]

    monkeypatch.setattr(SectionEditDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(SectionEditDialog, "section_name", lambda self: "Main Room")
    monkeypatch.setattr(SectionEditDialog, "section_type", lambda self: SectionType.PEAK)
    monkeypatch.setattr(SectionEditDialog, "section_color", lambda self: RekordboxColor.RED)

    _run_contract(window, lambda: window._multi_section_view._on_edit_section(block))

    assert section.name == "Main Room"
    assert section.section_type == SectionType.PEAK
    assert section.color == RekordboxColor.RED


def test_set_section_color_contract(window, section):
    _seed(window, section)

    _run_contract(
        window,
        lambda: window._edit_ctrl.set_section_color(section.id, RekordboxColor.BLUE),
    )

    assert section.color == RekordboxColor.BLUE


def test_apply_theme_contract(window, section):
    _seed(window, section)
    window._project.add_theme(
        SectionNameTheme(name="Festival", names={SectionType.PEAK: "Banger Hour"})
    )

    _run_contract(window, lambda: window._edit_ctrl.apply_theme("Festival"))

    assert section.name == "Banger Hour"


# --------------------------------------------------------------------------- project


def test_rename_project_contract(window):
    _run_contract(window, lambda: window._edit_ctrl.rename_project("Club Night"))

    assert window._project.name == "Club Night"


def test_recolor_all_by_type_contract(window):
    section = Section(name="Warm", section_type=SectionType.WARM_UP, color=RekordboxColor.RED)
    _seed(window, section)

    _run_contract(window, window._edit_ctrl.recolor_all_by_type)

    assert section.color == RekordboxColor.GREEN


# --------------------------------------------------------------------------- tracks


def test_add_track_contract(window, track, section):
    _seed(window, section)

    _run_contract(window, lambda: window._edit_ctrl.add_track(track, section.id))

    assert window._project.get_track(track.id) is track
    assert section.track_ids == [track.id]


def test_remove_track_contract(window, track, section):
    _seed(window, section, tracks=[track])

    _run_contract(window, lambda: window._edit_ctrl.remove_track(track.id, section.id))

    assert section.track_ids == []
    # The master store keeps the track; only the section reference is removed.
    assert window._project.get_track(track.id) is track


def test_move_track_cross_section_contract(window, track):
    src = Section(name="Src", section_type=SectionType.GENERAL)
    dst = Section(name="Dst", section_type=SectionType.GENERAL)
    src.add_track(track.id)
    _seed(window, src, dst, tracks=[track])

    _run_contract(
        window,
        lambda: window._edit_ctrl.move_track(track.id, src.id, dst.id, 0),
    )

    assert src.track_ids == []
    assert dst.track_ids == [track.id]


def test_move_tracks_batch_contract(window, track):
    other = Track(title="Atlas", artist="Bicep")
    src = Section(name="Src", section_type=SectionType.GENERAL)
    dst = Section(name="Dst", section_type=SectionType.GENERAL)
    src.add_track(track.id)
    src.add_track(other.id)
    _seed(window, src, dst, tracks=[track, other])

    _run_contract(
        window,
        lambda: window._edit_ctrl.move_tracks_batch([track.id, other.id], src.id, dst.id, 0),
    )

    assert src.track_ids == []
    assert dst.track_ids == [track.id, other.id]


def test_reorder_section_tracks_contract(window, track):
    other = Track(title="Atlas", artist="Bicep")
    section = Section(name="Peak", section_type=SectionType.GENERAL)
    section.add_track(track.id)
    section.add_track(other.id)
    _seed(window, section, tracks=[track, other])

    _run_contract(
        window,
        lambda: window._edit_ctrl.reorder_section_tracks(section.id, [other.id, track.id]),
    )

    assert section.track_ids == [other.id, track.id]


def test_apply_match_contract(window, track):
    _seed(window, tracks=[track])
    local = Track(
        title="Sundown",
        artist="DJ Koze",
        bpm=127.5,
        key="Am",
        filepath="/music/sundown.mp3",
        rekordbox_id=42,
    )

    _run_contract(window, lambda: window._edit_ctrl.apply_match(track.id, local))

    assert track.match_status == MatchStatus.MANUALLY_MATCHED
    assert track.rekordbox_id == 42
    assert track.filepath == "/music/sundown.mp3"


# --------------------------------------------------------------- transition notes


def test_transition_note_edit_contract(window, track, section):
    """The wired note-edit path: about_to_modify pushes one snapshot, then the
    note_edit write emits without pushing — one undo step for the whole edit."""
    _seed(window, section, tracks=[track])

    undo_before, spy = _observe_mutation(
        window,
        lambda: (
            window._transition_note.about_to_modify.emit(),
            window._transition_note.note_edit.emit(section.id, track.id, "smooth blend"),
        ),
    )

    _assert_funnel_contract(window, undo_before, spy)
    assert section.transition_notes[str(track.id)] == "smooth blend"


# ----------------------------------------------------------------- bypass sites
# These mutations currently bypass the funnel. The tests assert the intended
# contract (undoable + dirty + emitted) and are expected-change: they go green
# once the bypasses are migrated onto EditController.


def test_view_add_track_to_section_contract(window, track, section):
    _seed(window, section, tracks=[track])
    window._ctrl.project_changed.emit(window._project)  # view tracks the project
    new_track = Track(title="Late Night", artist="Floating Points")

    undo_before, spy = _observe_mutation(
        window,
        lambda: window._multi_section_view.add_track_to_section(new_track, section),
    )

    # Model end-state (holds today)
    assert new_track.id in section.track_ids
    assert window._project.get_track(new_track.id) is new_track
    # Intended funnel contract (not met while the view bypasses EditController)
    _assert_funnel_contract(window, undo_before, spy)


def test_theme_dialog_mutation_contract(window, monkeypatch):
    def _exec_with_mutation(dialog):
        dialog._project.add_theme(SectionNameTheme(name="Festival"))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(ThemeDialog, "exec", _exec_with_mutation)

    undo_before, spy = _observe_mutation(window, window._open_theme_dialog)

    # Model end-state (holds today)
    assert window._project.get_theme("Festival") is not None
    # Intended funnel contract (fails today: no project_changed emission)
    _assert_funnel_contract(window, undo_before, spy)
