"""Unit tests for controllers.undo_stack.UndoStack."""

from rekordbox_set_list_manager.controllers.undo_stack import UndoStack


def test_new_stack_is_empty():
    s: UndoStack[str] = UndoStack()
    assert not s.can_undo
    assert not s.can_redo


def test_push_enables_undo():
    s: UndoStack[str] = UndoStack()
    s.push("a")
    assert s.can_undo
    assert not s.can_redo


def test_undo_returns_pushed_value():
    s: UndoStack[str] = UndoStack()
    s.push("a")
    result = s.undo("b")
    assert result == "a"
    assert not s.can_undo


def test_undo_enables_redo():
    s: UndoStack[str] = UndoStack()
    s.push("a")
    s.undo("b")
    assert s.can_redo


def test_redo_returns_current_back_to_undo():
    s: UndoStack[str] = UndoStack()
    s.push("a")
    s.undo("b")  # undo stack now empty; redo has "b"
    result = s.redo("a")  # pass current "a" back; get "b" out
    assert result == "b"


def test_push_clears_redo():
    s: UndoStack[str] = UndoStack()
    s.push("a")
    s.undo("b")
    assert s.can_redo
    s.push("c")
    assert not s.can_redo


def test_undo_empty_returns_none():
    s: UndoStack[str] = UndoStack()
    assert s.undo("x") is None


def test_redo_empty_returns_none():
    s: UndoStack[str] = UndoStack()
    assert s.redo("x") is None


def test_max_size_enforced():
    s: UndoStack[int] = UndoStack(max_size=3)
    for i in range(10):
        s.push(i)
    r1 = s.undo(10)
    r2 = s.undo(r1)  # type: ignore[arg-type]
    r3 = s.undo(r2)  # type: ignore[arg-type]
    assert not s.can_undo
    assert r1 == 9
    assert r2 == 8
    assert r3 == 7


def test_no_duplicate_consecutive_pushes():
    s: UndoStack[str] = UndoStack()
    s.push("same")
    s.push("same")
    s.undo("same")
    assert not s.can_undo  # only one entry was stored


def test_clear_resets_everything():
    s: UndoStack[str] = UndoStack()
    s.push("a")
    s.push("b")
    s.undo("c")
    s.clear()
    assert not s.can_undo
    assert not s.can_redo
