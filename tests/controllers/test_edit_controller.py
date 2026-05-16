"""Tests for EditController."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock
from uuid import uuid4

from rekordbox_set_list_manager.controllers.edit_controller import EditController
from rekordbox_set_list_manager.controllers.undo_stack import UndoStack
from rekordbox_set_list_manager.models.enums import MatchStatus, RekordboxColor, SectionType
from rekordbox_set_list_manager.models.section import Section
from rekordbox_set_list_manager.models.track import Track

if TYPE_CHECKING:
    from rekordbox_set_list_manager.models.project import Project

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctrl(project: Project) -> tuple[EditController, MagicMock]:
    """Return an EditController backed by a mock ProjectController."""
    pc = MagicMock()
    pc.project = project

    def _restore(p: Project) -> None:
        pc.project = p

    pc.restore.side_effect = _restore

    ec = EditController.__new__(EditController)
    ec._ctrl = pc
    ec._stack = UndoStack()
    ec.project_changed = MagicMock()
    ec.project_changed.emit = MagicMock()
    return ec, pc


def _new_section(name: str = "Extra") -> Section:
    return Section(name=name, section_type=SectionType.GENERAL, color=RekordboxColor.NONE)


# ---------------------------------------------------------------------------
# Section mutations
# ---------------------------------------------------------------------------


class TestAddSection:
    def test_section_appended(self, project: Project) -> None:
        ec, _ = _make_ctrl(project)
        sec = _new_section()
        ec.add_section(sec)
        assert any(s.id == sec.id for s in ec._ctrl.project.sections)

    def test_project_changed_emitted(self, project: Project) -> None:
        ec, _ = _make_ctrl(project)
        ec.add_section(_new_section())
        ec.project_changed.emit.assert_called()

    def test_undo_restores_count(self, project: Project) -> None:
        ec, _ = _make_ctrl(project)
        before = len(project.sections)
        ec.add_section(_new_section())
        assert ec.can_undo
        ec.undo()
        assert len(ec._ctrl.project.sections) == before


class TestRemoveSection:
    def test_section_removed(self, project: Project, section: Section) -> None:
        ec, _ = _make_ctrl(project)
        ec.remove_section(section.id)
        assert not any(s.id == section.id for s in ec._ctrl.project.sections)

    def test_noop_for_unknown_id(self, project: Project) -> None:
        ec, _ = _make_ctrl(project)
        count_before = len(project.sections)
        ec.remove_section(uuid4())
        assert len(ec._ctrl.project.sections) == count_before


class TestRenameSection:
    def test_name_updated(self, project: Project, section: Section) -> None:
        ec, _ = _make_ctrl(project)
        ec.rename_section(section.id, "After Hours")
        assert project.get_section(section.id).name == "After Hours"

    def test_noop_for_unknown_id(self, project: Project) -> None:
        ec, _ = _make_ctrl(project)
        ec.rename_section(uuid4(), "Ghost")  # must not raise


class TestEditSection:
    def test_full_edit(self, project: Project, section: Section) -> None:
        ec, _ = _make_ctrl(project)
        ec.edit_section(section.id, "Closing", SectionType.CLOSING, RekordboxColor.BLUE)
        updated = project.get_section(section.id)
        assert updated.name == "Closing"
        assert updated.section_type == SectionType.CLOSING
        assert updated.color == RekordboxColor.BLUE

    def test_color_none_keeps_existing(self, project: Project, section: Section) -> None:
        ec, _ = _make_ctrl(project)
        original_color = section.color
        ec.edit_section(section.id, section.name, section.section_type, None)
        assert project.get_section(section.id).color == original_color


# ---------------------------------------------------------------------------
# Track mutations
# ---------------------------------------------------------------------------


class TestAddTrack:
    def test_track_in_project_and_section(self, project: Project, section: Section) -> None:
        ec, _ = _make_ctrl(project)
        t = Track(title="New", artist="A", bpm=130.0, key="Cm", duration=300)
        ec.add_track(t, section.id)
        assert t.id in project.tracks
        assert t.id in project.get_section(section.id).track_ids

    def test_noop_for_unknown_section(self, project: Project) -> None:
        ec, _ = _make_ctrl(project)
        t = Track(title="Ghost", artist="A", bpm=130.0, key="Cm", duration=300)
        ec.add_track(t, uuid4())
        assert t.id not in project.tracks


class TestRemoveTrack:
    def test_track_removed_from_section(
        self, project: Project, section: Section, track: Track
    ) -> None:
        ec, _ = _make_ctrl(project)
        ec.remove_track(track.id, section.id)
        assert track.id not in project.get_section(section.id).track_ids


class TestMoveTrack:
    def test_track_moves_between_sections(
        self, project: Project, section: Section, track: Track
    ) -> None:
        ec, _ = _make_ctrl(project)
        dst = Section(name="Closing", section_type=SectionType.CLOSING, color=RekordboxColor.NONE)
        project.add_section(dst)
        ec.move_track(track.id, section.id, dst.id, 0)
        assert track.id not in project.get_section(section.id).track_ids
        assert track.id in project.get_section(dst.id).track_ids


class TestReorderSectionTracks:
    def test_order_applied(self, project: Project, section: Section, track: Track) -> None:
        ec, _ = _make_ctrl(project)
        t2 = Track(title="T2", artist="A", bpm=130.0, key="Cm", duration=300)
        project.add_track(t2)
        section.add_track(t2.id)
        first_id, second_id = section.track_ids[0], section.track_ids[1]
        ec.reorder_section_tracks(section.id, [second_id, first_id])
        assert project.get_section(section.id).track_ids == [second_id, first_id]


# ---------------------------------------------------------------------------
# apply_match
# ---------------------------------------------------------------------------


class TestApplyMatch:
    def test_match_applied(self, project: Project, track: Track) -> None:
        ec, _ = _make_ctrl(project)
        local = Track(
            title=track.title,
            artist=track.artist,
            bpm=track.bpm,
            key=track.key,
            duration=track.duration,
            filepath="/music/file.mp3",
        )
        ec.apply_match(track.id, local)
        updated = project.get_track(track.id)
        assert updated.match_status == MatchStatus.MANUALLY_MATCHED
        assert updated.filepath == "/music/file.mp3"

    def test_clear_match(self, project: Project, track: Track) -> None:
        ec, _ = _make_ctrl(project)
        ec.apply_match(track.id, None)
        assert project.get_track(track.id).match_status == MatchStatus.UNMATCHED


# ---------------------------------------------------------------------------
# Undo / redo
# ---------------------------------------------------------------------------


class TestUndoRedo:
    def test_undo_restores_project(self, project: Project) -> None:
        ec, pc = _make_ctrl(project)
        original_count = len(project.sections)
        ec.add_section(_new_section())
        assert len(pc.project.sections) == original_count + 1
        ec.undo()
        assert len(pc.project.sections) == original_count

    def test_redo_reapplies(self, project: Project) -> None:
        ec, pc = _make_ctrl(project)
        original_count = len(project.sections)
        ec.add_section(_new_section())
        ec.undo()
        assert len(pc.project.sections) == original_count
        ec.redo()
        assert len(pc.project.sections) == original_count + 1

    def test_can_undo_false_initially(self, project: Project) -> None:
        ec, _ = _make_ctrl(project)
        assert not ec.can_undo

    def test_can_redo_false_initially(self, project: Project) -> None:
        ec, _ = _make_ctrl(project)
        assert not ec.can_redo

    def test_redo_cleared_after_new_mutation(self, project: Project) -> None:
        ec, _ = _make_ctrl(project)
        ec.add_section(_new_section("A"))
        ec.undo()
        assert ec.can_redo
        ec.add_section(_new_section("B"))
        assert not ec.can_redo

    def test_clear_empties_stacks(self, project: Project) -> None:
        ec, _ = _make_ctrl(project)
        ec.add_section(_new_section())
        assert ec.can_undo
        ec.clear()
        assert not ec.can_undo


# ---------------------------------------------------------------------------
# notify_changed
# ---------------------------------------------------------------------------


class TestNotifyChanged:
    def test_emits_project_changed(self, project: Project) -> None:
        ec, _ = _make_ctrl(project)
        ec.notify_changed()
        ec.project_changed.emit.assert_called()

    def test_marks_dirty(self, project: Project) -> None:
        ec, pc = _make_ctrl(project)
        ec.notify_changed()
        pc.mark_dirty.assert_called()


# ---------------------------------------------------------------------------
# set_transition_note
# ---------------------------------------------------------------------------


class TestSetTransitionNote:
    def test_sets_note_text(self, project: Project, section: Section, track: Track) -> None:
        ec, _ = _make_ctrl(project)
        ec.set_transition_note(section.id, track.id, "Great segue here")
        sec = project.get_section(section.id)
        assert sec.transition_notes.get(str(track.id)) == "Great segue here"

    def test_clears_note_on_empty_text(
        self, project: Project, section: Section, track: Track
    ) -> None:
        ec, _ = _make_ctrl(project)
        section.transition_notes[str(track.id)] = "old note"
        ec.set_transition_note(section.id, track.id, "")
        sec = project.get_section(section.id)
        assert str(track.id) not in sec.transition_notes

    def test_missing_section_is_no_op(self, project: Project, track: Track) -> None:
        ec, _ = _make_ctrl(project)
        # Should not raise
        ec.set_transition_note(uuid4(), track.id, "text")

    def test_emits_project_changed(self, project: Project, section: Section, track: Track) -> None:
        ec, _ = _make_ctrl(project)
        ec.set_transition_note(section.id, track.id, "note")
        ec.project_changed.emit.assert_called()

    def test_marks_dirty(self, project: Project, section: Section, track: Track) -> None:
        ec, pc = _make_ctrl(project)
        ec.set_transition_note(section.id, track.id, "note")
        pc.mark_dirty.assert_called()
