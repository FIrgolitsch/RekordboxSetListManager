"""Panel for editing transition notes attached to a track in a section."""

from __future__ import annotations

from uuid import UUID

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QTextEdit, QVBoxLayout, QWidget

from rekordbox_set_list_manager.models.section import Section


class TransitionNoteWidget(QWidget):
    """Editable text area showing the transition note after the selected track.

    The note is stored in ``section.transition_notes[str(track_id)]`` and
    written back via :attr:`note_edit` on every keystroke so the caller can
    route the write through :class:`EditController` without holding a stale
    Section reference.

    :attr:`about_to_modify` is emitted once per editing session (first
    keystroke after a new track is selected) so the MainWindow can push an
    undo snapshot before any text is changed.

    :attr:`note_edit` carries ``(section_id, track_id, text)`` on every
    keystroke; the receiver should call
    ``EditController.set_transition_note(section_id, track_id, text)``.
    """

    about_to_modify = Signal()  # emitted once per edit session, before first change
    note_edit = Signal(object, object, str)  # (section_id: UUID, track_id: UUID, text: str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._section_id: UUID | None = None
        self._track_id: UUID | None = None
        self._updating = False
        self._edit_in_progress = False  # True after first keystroke of a session

        label = QLabel("Transition note (after this track):")
        self._edit = QTextEdit()
        self._edit.setPlaceholderText("Notes for the transition to the next track…")
        self._edit.setMaximumHeight(68)
        self._edit.textChanged.connect(self._on_text_changed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 2)
        layout.setSpacing(2)
        layout.addWidget(label)
        layout.addWidget(self._edit)

        self.set_track(None, None)

    # ------------------------------------------------------------------ public

    def set_track(self, track_id: UUID | None, section: Section | None) -> None:
        """Load the note for *track_id* / *section* into the editor."""
        self._section_id = section.id if section is not None else None
        self._track_id = track_id
        self._edit_in_progress = False  # reset: a new track starts a new edit session
        self._updating = True
        if track_id is not None and section is not None:
            note = section.transition_notes.get(str(track_id), "")
            self._edit.setPlainText(note)
            self._edit.setEnabled(True)
        else:
            self._edit.clear()
            self._edit.setEnabled(False)
        self._updating = False

    # ----------------------------------------------------------------- private

    def _on_text_changed(self) -> None:
        if self._updating or self._section_id is None or self._track_id is None:
            return
        if not self._edit_in_progress:
            # First keystroke of this session — capture undo state before writing.
            self._edit_in_progress = True
            self.about_to_modify.emit()
        text = self._edit.toPlainText()
        self.note_edit.emit(self._section_id, self._track_id, text)
