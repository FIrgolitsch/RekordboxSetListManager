"""Generic JSON-snapshot undo/redo stack."""

from __future__ import annotations

_DEFAULT_MAX = 50


class UndoStack[T]:
    """Undo/redo stack that stores serialised snapshots of type *T*.

    Usage::

        stack: UndoStack[str] = UndoStack()

        # Before a mutation:
        stack.push(current_snapshot)

        # Undo:
        prev = stack.undo(current_snapshot)

        # Redo:
        next_ = stack.redo(current_snapshot)
    """

    def __init__(self, max_size: int = _DEFAULT_MAX) -> None:
        """Initialise an empty undo/redo stack with *max_size* capacity."""
        self._max = max_size
        self._undo: list[T] = []
        self._redo: list[T] = []

    # ---------------------------------------------------------------- public

    @property
    def can_undo(self) -> bool:
        """Return True if there are undo steps available."""
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        """Return True if there are redo steps available."""
        return bool(self._redo)

    def push(self, snapshot: T) -> None:
        """Record *snapshot* before a mutation.  Clears the redo stack.

        Parameters
        ----------
        snapshot : T
            The serialised project state to push onto the undo history.

        """
        if self._undo and self._undo[-1] == snapshot:
            return  # no-op: nothing changed
        self._undo.append(snapshot)
        if len(self._undo) > self._max:
            self._undo.pop(0)
        self._redo.clear()

    def undo(self, current: T) -> T | None:
        """Undo one step.  Returns the previous snapshot, or *None* if empty.

        The caller must pass the *current* snapshot so it can be pushed to the
        redo stack.

        Parameters
        ----------
        current : T
            The current serialised state, saved to the redo stack.

        Returns
        -------
        T | None
            The previous snapshot to restore, or ``None`` if the undo stack is
            empty.

        """
        if not self._undo:
            return None
        self._redo.append(current)
        return self._undo.pop()

    def redo(self, current: T) -> T | None:
        """Redo one step.  Returns the next snapshot, or *None* if empty.

        The caller must pass the *current* snapshot so it can be pushed back
        to the undo stack.

        Parameters
        ----------
        current : T
            The current serialised state, saved back to the undo stack.

        Returns
        -------
        T | None
            The next snapshot to restore, or ``None`` if the redo stack is
            empty.

        """
        if not self._redo:
            return None
        self._undo.append(current)
        return self._redo.pop()

    def clear(self) -> None:
        """Discard all undo and redo history."""
        self._undo.clear()
        self._redo.clear()
