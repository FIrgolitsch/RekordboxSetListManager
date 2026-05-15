"""EditController — single entry point for all project mutations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal

from rekordbox_set_list_manager.controllers.track_match import apply_track_match
from rekordbox_set_list_manager.controllers.undo_stack import UndoStack
from rekordbox_set_list_manager.models.project import Project

if TYPE_CHECKING:
    from uuid import UUID

    from rekordbox_set_list_manager.controllers.project_controller import ProjectController
    from rekordbox_set_list_manager.models.enums import RekordboxColor, SectionType
    from rekordbox_set_list_manager.models.section import Section
    from rekordbox_set_list_manager.models.track import Track


class EditController(QObject):
    """Routes all model mutations through a single controller with undo/redo.

    Every public method:
      1. Pushes a JSON snapshot onto the undo stack.
      2. Mutates the project.
      3. Emits ``project_changed``.

    Signals
    -------
    project_changed : emitted after every mutation.
    """

    project_changed = Signal()

    def __init__(
        self,
        ctrl: ProjectController,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._ctrl = ctrl
        self._stack: UndoStack[str] = UndoStack()

    # ------------------------------------------------------------------ props

    @property
    def can_undo(self) -> bool:
        return self._stack.can_undo

    @property
    def can_redo(self) -> bool:
        return self._stack.can_redo

    # --------------------------------------------------------- section methods

    def add_section(self, section: Section) -> None:
        proj = self._require()
        if proj is None:
            return
        self._push(proj)
        proj.add_section(section)
        self._emit()

    def remove_section(self, section_id: UUID) -> None:
        proj = self._require()
        if proj is None:
            return
        self._push(proj)
        proj.remove_section(section_id)
        self._emit()

    def rename_section(self, section_id: UUID, name: str) -> None:
        proj = self._require()
        if proj is None:
            return
        sec = proj.get_section(section_id)
        if sec is None:
            return
        self._push(proj)
        sec.name = name
        self._emit()

    def set_section_color(self, section_id: UUID, color: RekordboxColor) -> None:
        proj = self._require()
        if proj is None:
            return
        sec = proj.get_section(section_id)
        if sec is None:
            return
        self._push(proj)
        sec.color = color
        self._emit()

    def edit_section(
        self,
        section_id: UUID,
        name: str,
        section_type: SectionType,
        color: RekordboxColor | None,
    ) -> None:
        """Rename + retype + recolor a section in one atomic operation."""
        proj = self._require()
        if proj is None:
            return
        sec = proj.get_section(section_id)
        if sec is None:
            return
        self._push(proj)
        sec.name = name
        if section_type != sec.section_type:
            sec.section_type = section_type
            sec.color = proj.default_color_for(section_type)
        if color is not None and color != sec.color:
            sec.color = color
        self._emit()

    def move_section(self, section_id: UUID, new_index: int) -> None:
        proj = self._require()
        if proj is None:
            return
        self._push(proj)
        proj.move_section(section_id, new_index)
        self._emit()

    def apply_theme(self, theme_name: str) -> None:
        proj = self._require()
        if proj is None:
            return
        self._push(proj)
        proj.apply_theme(theme_name)
        self._emit()

    def recolor_all_by_type(self) -> int:
        """Recolor every section to its type's default color; returns sections changed."""
        proj = self._require()
        if proj is None:
            return 0
        # Compute changes before touching the model
        updates = [
            (sec, proj.default_color_for(sec.section_type))
            for sec in proj.sections
            if sec.color != proj.default_color_for(sec.section_type)
        ]
        if not updates:
            return 0
        self._push(proj)
        for sec, color in updates:
            sec.color = color
        self._emit()
        return len(updates)

    # ---------------------------------------------------------- track methods

    def add_track(self, track: Track, section_id: UUID) -> None:
        proj = self._require()
        if proj is None:
            return
        sec = proj.get_section(section_id)
        if sec is None:
            return
        self._push(proj)
        proj.add_track(track)
        sec.add_track(track.id)
        self._emit()

    def remove_track(self, track_id: UUID, section_id: UUID) -> None:
        proj = self._require()
        if proj is None:
            return
        sec = proj.get_section(section_id)
        if sec is None:
            return
        self._push(proj)
        sec.remove_track(track_id)
        self._emit()

    def move_track(
        self,
        track_id: UUID,
        from_section_id: UUID,
        to_section_id: UUID,
        index: int,
    ) -> None:
        proj = self._require()
        if proj is None:
            return
        src = proj.get_section(from_section_id)
        dst = proj.get_section(to_section_id)
        if src is None or dst is None:
            return
        self._push(proj)
        src.remove_track(track_id)
        dst.track_ids.insert(max(0, min(index, len(dst.track_ids))), track_id)
        self._emit()

    def reorder_section_tracks(self, section_id: UUID, track_ids: list[UUID]) -> None:
        proj = self._require()
        if proj is None:
            return
        sec = proj.get_section(section_id)
        if sec is None:
            return
        self._push(proj)
        sec.track_ids = list(track_ids)
        self._emit()

    def apply_match(self, track_id: UUID, local: Track | None) -> None:
        proj = self._require()
        if proj is None:
            return
        track = proj.get_track(track_id)
        if track is None:
            return
        self._push(proj)
        apply_track_match(track, local)
        self._emit()

    # --------------------------------------------------------------- undo/redo

    def undo(self) -> None:
        proj = self._require()
        if proj is None or not self._stack.can_undo:
            return
        snap = self._stack.undo(proj.model_dump_json())
        if snap is not None:
            self._ctrl.restore(Project.model_validate_json(snap))
            self.project_changed.emit()

    def redo(self) -> None:
        proj = self._require()
        if proj is None or not self._stack.can_redo:
            return
        snap = self._stack.redo(proj.model_dump_json())
        if snap is not None:
            self._ctrl.restore(Project.model_validate_json(snap))
            self.project_changed.emit()

    def clear(self) -> None:
        self._stack.clear()

    def push_snapshot(self) -> None:
        """Push current project state onto undo stack (for external callers)."""
        proj = self._require()
        if proj is not None:
            self._push(proj)

    def notify_changed(self) -> None:
        """Mark project dirty and emit project_changed (for external callers).

        Use when a mutation was performed outside the EditController but should
        still be reflected in the undo stack bookkeeping and UI state.
        """
        self._emit()

    def set_transition_note(self, section_id: UUID, track_id: UUID, text: str) -> None:
        """Update a transition note without pushing a snapshot.

        The caller is responsible for pushing a snapshot beforehand
        (typically via :meth:`push_snapshot` triggered by ``about_to_modify``).
        """
        proj = self._require()
        if proj is None:
            return
        sec = proj.get_section(section_id)
        if sec is None:
            return
        key = str(track_id)
        if text:
            sec.transition_notes[key] = text
        else:
            sec.transition_notes.pop(key, None)
        self._emit()

    def move_tracks_batch(
        self,
        track_ids: list[UUID],
        from_section_id: UUID,
        to_section_id: UUID,
        dest_index: int,
    ) -> None:
        """Move multiple tracks from one section to another in a single undo step."""
        proj = self._require()
        if proj is None:
            return
        src = proj.get_section(from_section_id)
        dst = proj.get_section(to_section_id)
        if src is None or dst is None:
            return
        self._push(proj)
        for tid in track_ids:
            if tid in src.track_ids:
                src.track_ids.remove(tid)
        clamped = max(0, min(dest_index, len(dst.track_ids)))
        for i, tid in enumerate(track_ids):
            dst.track_ids.insert(clamped + i, tid)
        self._emit()

    # ---------------------------------------------------------------- private

    def _require(self) -> Project | None:
        return self._ctrl.project

    def _push(self, proj: Project) -> None:
        self._stack.push(proj.model_dump_json())

    def _emit(self) -> None:
        self._ctrl.mark_dirty()
        self.project_changed.emit()
