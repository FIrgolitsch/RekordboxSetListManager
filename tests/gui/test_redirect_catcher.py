"""Tests for the PKCE redirect clipboard watcher."""

from __future__ import annotations

from PySide6.QtGui import QGuiApplication

from rekordbox_set_list_manager.gui.widgets.streaming.redirect_catcher import (
    REDIRECT_PREFIX,
    RedirectCatcher,
)


def test_catcher_detects_redirect_url(qtbot):
    catcher = RedirectCatcher()
    seen: list[str] = []
    catcher.found.connect(seen.append)
    catcher.start()

    QGuiApplication.clipboard().setText(f"{REDIRECT_PREFIX}?code=abc123")

    qtbot.waitUntil(lambda: bool(seen), timeout=3000)
    assert seen[0].endswith("code=abc123")


def test_catcher_ignores_unrelated_clipboard(qtbot):
    catcher = RedirectCatcher()
    seen: list[str] = []
    catcher.found.connect(seen.append)
    catcher.start()

    QGuiApplication.clipboard().setText("https://example.com/not-a-redirect")

    qtbot.wait(700)  # longer than a poll interval
    assert seen == []


def test_catcher_stops_after_found(qtbot):
    catcher = RedirectCatcher()
    seen: list[str] = []
    catcher.found.connect(seen.append)
    catcher.start()

    QGuiApplication.clipboard().setText(f"{REDIRECT_PREFIX}?code=first")
    qtbot.waitUntil(lambda: bool(seen), timeout=3000)
    assert not catcher._timer.isActive()


def test_catcher_ignores_baseline_clipboard(qtbot):
    """A redirect URL already on the clipboard when watching starts is
    stale (its code belongs to a dead verifier) — never auto-capture it."""
    stale = f"{REDIRECT_PREFIX}?code=stale"
    QGuiApplication.clipboard().setText(stale)
    catcher = RedirectCatcher()
    seen: list[str] = []
    catcher.found.connect(seen.append)
    catcher.start()

    qtbot.wait(700)  # longer than a poll interval
    assert seen == []

    # A different (fresh) redirect URL is still captured.
    QGuiApplication.clipboard().setText(f"{REDIRECT_PREFIX}?code=fresh")
    qtbot.waitUntil(lambda: bool(seen), timeout=3000)
    assert seen[0].endswith("code=fresh")
