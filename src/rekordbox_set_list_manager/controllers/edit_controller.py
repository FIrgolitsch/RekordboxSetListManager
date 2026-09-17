"""EditController — single entry point for all project mutations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal

from rekordbox_set_list_manager.controllers.track_match import apply_track_match
from rekordbox_set_list_manager.controllers.undo_stack import UndoStack, fingerprint
from rekordbox_set_list_manager.models.project import Project

if TYPE_CHECKING:
    from collections.abc import Callable
    from uuid import UUID

    from rekordbox_set_list_manager.controllers.project_controller import ProjectController
    from rekordbox_set_list_manager.models.enums import RekordboxColor, SectionType
    from rekordbox_set_list_manager.models.section import Section
    from rekordbox_set_list_manager.models.track import Track


class EditController(QObject):
    """Routes all model mutations through a single controller with undo/redo.

    Every public method delegates to :meth:`apply_mutation`, which
    fingerprints the project before and after the mutation: a changed
    fingerprint pushes the pre-state snapshot onto the undo stack, marks
    the project dirty, and emits ``project_changed``; an unchanged
    fingerprint records nothing.

    Signals
    -------
    project_changed : emitted after every effective mutation.
    """

    project_changed = Signal()

    def __init__(
        self,
        ctrl: ProjectController,
        parent: QObject | None = None,
    ) -> None:
        """Initialise the edit controller with *ctrl* and an empty undo stack."""
        super().__init__(parent)
        self._ctrl = ctrl
        self._stack: UndoStack[str] = UndoStack()
        self._pending_merge: tuple[str, Project, bytes] | None = None

    # ------------------------------------------------------------------ props

    @property
    def can_undo(self) -> bool:
        """Return True if there are undo steps available."""
        return self._stack.can_undo

    @property
    def can_redo(self) -> bool:
        """Return True if there are redo steps available."""
        return self._stack.can_redo

    # ---------------------------------------------------------------- funnel

    def apply_mutation[T](
        self, fn: Callable[[Project], T], *, merge_key: str | None = None
    ) -> T | None:
        """Run *fn* on the project through the single audited mutation funnel.

        Ordering: fingerprint the current state, run ``fn``, fingerprint
        again.  An unchanged fingerprint discards the call — no snapshot,
        no dirty flag, no ``project_changed`` emission — but *fn*'s result
        is still returned.  A changed fingerprint pushes the pre-state
        snapshot, marks the project dirty, and emits ``project_changed``.

        If *fn* raises after mutating, the partial state is recorded (the
        pre-state snapshot is pushed and ``project_changed`` emitted) before
        the exception re-raises unchanged; a raise with net-zero change
        records nothing.

        Consecutive calls sharing *merge_key* reuse the pending undo entry
        instead of pushing one per call — the ``QUndoCommand.mergeWith``
        analogue.  Coalescing is key-based, not session-based: returning to
        the same key with no intervening mutation merges into the old run.
        The run resets on undo/redo/clear, on any different-key or keyless
        call, and whenever the ``Project`` instance is swapped (undo/redo/
        restore/new/load) via identity comparison — a stale run can never
        write back an old project's snapshot.

        Parameters
        ----------
        fn : Callable[[Project], T]
            The mutation to run against the live ``Project``.  Its return
            value is passed through to the caller.
        merge_key : str | None, optional
            Coalescing key.  Consecutive calls with the same key share one
            undo entry instead of pushing one entry per call.

        Returns
        -------
        T | None
            *fn*'s return value, or ``None`` when no project is loaded.

        """
        proj = self._require()
        if proj is None:
            return None

        pending = self._pending_merge
        if (
            pending is not None
            and merge_key is not None
            and pending[0] == merge_key
            and pending[1] is proj
        ):
            try:
                result = fn(proj)
            except Exception:
                # The run's pre-state snapshot is already on the stack, so a
                # second push would add a phantom undo entry — emit only, so
                # the partial continuation stays undo-reachable via that
                # snapshot.  The exception breaks the run either way.
                if fingerprint(proj.model_dump_json()) != pending[2]:
                    self._emit()
                self._pending_merge = None
                raise
            fp_after = fingerprint(proj.model_dump_json())
            if fp_after == pending[2]:
                return result  # no-op continuation: nothing changed
            self._pending_merge = (merge_key, proj, fp_after)
            self._emit()
            return result

        self._pending_merge = None  # non-matching call closes any open run
        before = proj.model_dump_json()
        fp_before = fingerprint(before)
        try:
            result = fn(proj)
        except Exception:
            # Record the partial mutation so every changed state stays
            # undo-reachable, then propagate the failure to the caller.
            if fingerprint(proj.model_dump_json()) != fp_before:
                self._stack.push(before, digest=fp_before)
                # Settle the merge baseline before emitting so a reentrant
                # slot sees the final funnel state.
                self._pending_merge = None
                self._emit()
            raise
        fp_after = fingerprint(proj.model_dump_json())
        if fp_after == fp_before:
            return result  # fingerprint no-op: discard, return result
        self._stack.push(before, digest=fp_before)
        # Record the merge baseline before emitting: a reentrant slot
        # mutating with the same key must coalesce against this run, and
        # its own baseline update must not be overwritten afterwards.
        if merge_key is not None:
            self._pending_merge = (merge_key, proj, fp_after)
        self._emit()
        return result

    # --------------------------------------------------------- section methods

    def add_section(self, section: Section) -> None:
        """Append a new section to the project.

        Parameters
        ----------
        section : Section
            The section to append.

        """

        def _mutate(proj: Project) -> None:
            proj.add_section(section)

        self.apply_mutation(_mutate)

    def remove_section(self, section_id: UUID) -> None:
        """Remove the section identified by *section_id* from the project.

        Parameters
        ----------
        section_id : UUID
            The ID of the section to remove.

        """

        def _mutate(proj: Project) -> None:
            proj.remove_section(section_id)

        self.apply_mutation(_mutate)

    def rename_project(self, name: str) -> None:
        """Rename the project.

        Parameters
        ----------
        name : str
            The new project name.

        """

        def _mutate(proj: Project) -> None:
            proj.name = name

        self.apply_mutation(_mutate)

    def rename_section(self, section_id: UUID, name: str) -> None:
        """Rename the section identified by *section_id*.

        Parameters
        ----------
        section_id : UUID
            The ID of the section to rename.
        name : str
            The new display name.

        """

        def _mutate(proj: Project) -> None:
            sec = proj.get_section(section_id)
            if sec is None:
                return
            sec.name = name

        self.apply_mutation(_mutate)

    def set_section_color(self, section_id: UUID, color: RekordboxColor) -> None:
        """Set the display color of the section identified by *section_id*.

        Parameters
        ----------
        section_id : UUID
            The ID of the section to recolor.
        color : RekordboxColor
            The new color to assign.

        """

        def _mutate(proj: Project) -> None:
            sec = proj.get_section(section_id)
            if sec is None:
                return
            sec.color = color

        self.apply_mutation(_mutate)

    def edit_section(
        self,
        section_id: UUID,
        name: str,
        section_type: SectionType,
        color: RekordboxColor | None,
    ) -> None:
        """Rename + retype + recolor a section in one atomic operation.

        Parameters
        ----------
        section_id : UUID
            The ID of the section to edit.
        name : str
            The new display name.
        section_type : SectionType
            The new section type.  Changing the type also resets the color to
            the project's default for that type unless *color* overrides it.
        color : RekordboxColor | None
            An explicit color override, or ``None`` to use the type default.

        """

        def _mutate(proj: Project) -> None:
            sec = proj.get_section(section_id)
            if sec is None:
                return
            sec.name = name
            if section_type != sec.section_type:
                sec.section_type = section_type
                sec.color = proj.default_color_for(section_type)
            if color is not None and color != sec.color:
                sec.color = color

        self.apply_mutation(_mutate)

    def move_section(self, section_id: UUID, new_index: int) -> None:
        """Move the section identified by *section_id* to *new_index*.

        Parameters
        ----------
        section_id : UUID
            The ID of the section to reposition.
        new_index : int
            Target zero-based position in the sections list.

        """

        def _mutate(proj: Project) -> None:
            if proj.get_section(section_id) is None:
                return
            proj.move_section(section_id, new_index)

        self.apply_mutation(_mutate)

    def apply_theme(self, theme_name: str) -> None:
        """Apply the named colour/name theme to all sections.

        Parameters
        ----------
        theme_name : str
            Name of the theme preset to apply.

        """

        def _mutate(proj: Project) -> None:
            if proj.get_theme(theme_name) is None:
                return
            proj.apply_theme(theme_name)

        self.apply_mutation(_mutate)

    def recolor_all_by_type(self) -> int:
        """Recolor every section to its type's default color; returns sections changed.

        Returns
        -------
        int
            The number of sections whose color was updated.

        """

        def _mutate(proj: Project) -> int:
            changed = 0
            for sec in proj.sections:
                color = proj.default_color_for(sec.section_type)
                if sec.color != color:
                    sec.color = color
                    changed += 1
            return changed

        result = self.apply_mutation(_mutate)
        return result if result is not None else 0

    # ---------------------------------------------------------- track methods

    def add_track(self, track: Track, section_id: UUID) -> None:
        """Add *track* to the project and append it to the given section.

        Parameters
        ----------
        track : Track
            The track to register in the project and append to the section.
        section_id : UUID
            The ID of the section to append the track to.

        """

        def _mutate(proj: Project) -> None:
            sec = proj.get_section(section_id)
            if sec is None:
                return
            proj.add_track(track)
            sec.add_track(track.id)

        self.apply_mutation(_mutate)

    def remove_track(self, track_id: UUID, section_id: UUID) -> None:
        """Remove *track_id* from the specified section.

        Parameters
        ----------
        track_id : UUID
            The ID of the track to remove from the section.
        section_id : UUID
            The ID of the section containing the track.

        """

        def _mutate(proj: Project) -> None:
            sec = proj.get_section(section_id)
            if sec is None:
                return
            sec.remove_track(track_id)

        self.apply_mutation(_mutate)

    def move_track(
        self,
        track_id: UUID,
        from_section_id: UUID,
        to_section_id: UUID,
        index: int,
    ) -> None:
        """Move *track_id* from one section to another at a given index.

        Parameters
        ----------
        track_id : UUID
            The ID of the track to move.
        from_section_id : UUID
            The section the track is currently in.
        to_section_id : UUID
            The destination section.
        index : int
            Target zero-based insertion index in the destination section.

        """

        def _mutate(proj: Project) -> None:
            src = proj.get_section(from_section_id)
            dst = proj.get_section(to_section_id)
            if src is None or dst is None or track_id not in src.track_ids:
                return
            if src is dst:
                # Reposition without touching transition_notes — a
                # remove/reinsert through Section.remove_track would drop
                # the note even though the track never left the section.
                clamped = max(0, min(index, len(src.track_ids) - 1))
                src.move_track(track_id, clamped)
                return
            # track_ids.remove (not Section.remove_track) keeps the note
            # alive, matching move_tracks_batch.
            src.track_ids.remove(track_id)
            dst.track_ids.insert(max(0, min(index, len(dst.track_ids))), track_id)

        self.apply_mutation(_mutate)

    def reorder_section_tracks(self, section_id: UUID, track_ids: list[UUID]) -> None:
        """Replace the track order in a section with the supplied *track_ids* list.

        Parameters
        ----------
        section_id : UUID
            The ID of the section whose track order to replace.
        track_ids : list[UUID]
            The new ordered list of track IDs for the section.

        """

        def _mutate(proj: Project) -> None:
            sec = proj.get_section(section_id)
            if sec is None:
                return
            sec.track_ids = list(track_ids)

        self.apply_mutation(_mutate)

    def apply_match(self, track_id: UUID, local: Track | None) -> None:
        """Apply a local-track match result to *track_id* in the project.

        Parameters
        ----------
        track_id : UUID
            The streaming track to update.
        local : Track | None
            The local Rekordbox track to copy metadata from, or ``None`` to
            clear the existing match.

        """

        def _mutate(proj: Project) -> None:
            track = proj.get_track(track_id)
            if track is None:
                return
            apply_track_match(track, local)

        self.apply_mutation(_mutate)

    # --------------------------------------------------------------- undo/redo

    def undo(self) -> None:
        """Restore the project to the previous snapshot."""
        self._pending_merge = None
        proj = self._require()
        if proj is None or not self._stack.can_undo:
            return
        snap = self._stack.undo(proj.model_dump_json())
        if snap is not None:
            self._ctrl.restore(Project.model_validate_json(snap))
            self.project_changed.emit()

    def redo(self) -> None:
        """Re-apply the next snapshot after an undo."""
        self._pending_merge = None
        proj = self._require()
        if proj is None or not self._stack.can_redo:
            return
        snap = self._stack.redo(proj.model_dump_json())
        if snap is not None:
            self._ctrl.restore(Project.model_validate_json(snap))
            self.project_changed.emit()

    def clear(self) -> None:
        """Clear the undo/redo history."""
        self._pending_merge = None
        self._stack.clear()

    def set_transition_note(self, section_id: UUID, track_id: UUID, text: str) -> None:
        """Update a transition note through the mutation funnel.

        Every call is funneled with a per-note merge key so consecutive
        edits to the same note coalesce into a single undo entry.

        Parameters
        ----------
        section_id : UUID
            The section containing the track.
        track_id : UUID
            The track after which the transition note appears.
        text : str
            The note text.  An empty string removes the note.

        """

        def _mutate(proj: Project) -> None:
            sec = proj.get_section(section_id)
            if sec is None:
                return
            key = str(track_id)
            if text:
                sec.transition_notes[key] = text
            else:
                sec.transition_notes.pop(key, None)

        self.apply_mutation(_mutate, merge_key=f"note:{section_id}:{track_id}")

    def move_tracks_batch(
        self,
        track_ids: list[UUID],
        from_section_id: UUID,
        to_section_id: UUID,
        dest_index: int,
    ) -> None:
        """Move multiple tracks from one section to another in a single undo step.

        Parameters
        ----------
        track_ids : list[UUID]
            Ordered list of track IDs to move.
        from_section_id : UUID
            The section the tracks are being moved from.
        to_section_id : UUID
            The destination section.
        dest_index : int
            Target zero-based insertion index in the destination section.

        """

        def _mutate(proj: Project) -> None:
            src = proj.get_section(from_section_id)
            dst = proj.get_section(to_section_id)
            if src is None or dst is None:
                return
            # Only move what was actually in the source — inserting
            # non-members would duplicate IDs or reference ghost tracks.
            removed = []
            for tid in track_ids:
                if tid in src.track_ids:
                    src.track_ids.remove(tid)
                    removed.append(tid)
            clamped = max(0, min(dest_index, len(dst.track_ids)))
            for i, tid in enumerate(removed):
                dst.track_ids.insert(clamped + i, tid)

        self.apply_mutation(_mutate)

    # ---------------------------------------------------------------- private

    def _require(self) -> Project | None:
        return self._ctrl.project

    def _emit(self) -> None:
        self._ctrl.mark_dirty()
        self.project_changed.emit()
