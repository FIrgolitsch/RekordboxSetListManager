"""Menu bar construction for the main window."""

# This module is MainWindow's menu-building companion: it intentionally
# assigns and reads ``_act_*``/``_*_menu``/controller members on the window
# object so tests and facades can keep reaching them as window attributes.
# ruff: noqa: SLF001

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtGui import QAction, QKeySequence

from rekordbox_set_list_manager.utils.config import get_recent_files

if TYPE_CHECKING:
    from rekordbox_set_list_manager.gui.main_window import MainWindow


def build_menus(window: MainWindow) -> None:
    """Build the File/Edit/Project menus, assigning actions on *window*.

    Parameters
    ----------
    window : MainWindow
        The main window; ``_act_*`` and ``_*_menu`` attributes are assigned
        directly on it so existing call sites keep working.

    """
    bar = window.menuBar()

    window._file_menu = bar.addMenu("&File")
    window._act_new = _action(
        window, "&New", QKeySequence.StandardKey.New, window._file_ctrl.new_project
    )
    window._act_open = _action(
        window, "&Open…", QKeySequence.StandardKey.Open, window._file_ctrl.open
    )
    window._act_save = _action(window, "&Save", QKeySequence.StandardKey.Save, window._save)
    window._act_save_as = _action(
        window, "Save &As…", QKeySequence.StandardKey.SaveAs, window._save_as
    )
    window._file_menu.addAction(window._act_new)
    window._file_menu.addAction(window._act_open)
    window._recent_menu = window._file_menu.addMenu("Open &Recent")
    window._file_menu.addSeparator()
    window._file_menu.addAction(window._act_save)
    window._file_menu.addAction(window._act_save_as)
    window._file_menu.addSeparator()
    window._act_quit = _action(window, "&Quit", QKeySequence.StandardKey.Quit, window.close)
    window._file_menu.addAction(window._act_quit)
    update_recent_menu(window)

    window._edit_menu = bar.addMenu("&Edit")
    window._act_undo = _action(window, "&Undo", QKeySequence.StandardKey.Undo, window._undo)
    window._act_undo.setEnabled(False)
    window._act_redo = _action(window, "Re&do", QKeySequence.StandardKey.Redo, window._redo)
    window._act_redo.setEnabled(False)
    window._edit_menu.addAction(window._act_undo)
    window._edit_menu.addAction(window._act_redo)
    window._edit_menu.addSeparator()
    window._act_add_track = _action(
        window, "Add Track…", QKeySequence("Ctrl+T"), window._track_ctrl.add_track
    )
    window._act_add_section = _action(
        window,
        "Add Section",
        QKeySequence("Ctrl+Shift+A"),
        window._multi_section_view.add_section_interactive,
    )
    window._act_filter = _action(
        window, "Filter Tracks", QKeySequence("Ctrl+F"), window._multi_section_view.focus_filter
    )
    window._edit_menu.addAction(window._act_add_track)
    window._edit_menu.addAction(window._act_add_section)
    window._edit_menu.addAction(window._act_filter)

    window._project_menu = bar.addMenu("&Project")
    window._act_rename = _action(
        window, "Re&name Project…", None, window._project_actions.rename_project
    )
    window._project_menu.addAction(window._act_rename)
    window._project_menu.addSeparator()
    window._act_export = _action(
        window, "&Export to Rekordbox…", None, window._project_actions.export
    )
    window._act_export_setlist = _action(
        window,
        "Export Setlist Files…",
        None,
        window._project_actions.export_setlist,
    )
    window._act_import = _action(
        window,
        "&Import from Streaming Service…",
        None,
        window._import_ctrl.import_streaming,
    )
    window._act_settings = _action(
        window, "&Service Settings…", None, window._project_actions.open_settings
    )
    window._act_themes = _action(
        window, "Section Name &Themes…", None, window._project_actions.open_theme_dialog
    )
    window._act_rematch_xml = _action(
        window, "Re-match with Rekordbox &XML…", None, window._rematch_ctrl.rematch_xml
    )
    window._act_rematch_db = _action(
        window, "Re-match with Rekordbox &DB", None, window._rematch_ctrl.rematch_db
    )
    window._act_rematch_manual = _action(
        window, "&Manual Re-match…", None, window._rematch_ctrl.rematch_manual
    )
    window._act_update_spotify = _action(
        window,
        "&Update Spotify Playlist Order",
        None,
        window._spotify_sync_ctrl.update_order,
    )
    window._project_menu.addAction(window._act_export)
    window._project_menu.addAction(window._act_export_setlist)
    window._project_menu.addSeparator()
    window._project_menu.addAction(window._act_import)
    window._project_menu.addAction(window._act_settings)
    window._project_menu.addSeparator()
    rematch_menu = window._project_menu.addMenu("Re-match with &Rekordbox")
    rematch_menu.addAction(window._act_rematch_xml)
    rematch_menu.addAction(window._act_rematch_db)
    rematch_menu.addSeparator()
    rematch_menu.addAction(window._act_rematch_manual)
    window._act_recolor = _action(
        window,
        "&Recolor sections by type…",
        None,
        window._project_actions.recolor_by_type,
    )
    window._project_menu.addSeparator()
    window._project_menu.addAction(window._act_update_spotify)
    window._project_menu.addSeparator()
    window._project_menu.addAction(window._act_themes)
    window._project_menu.addAction(window._act_recolor)


def update_recent_menu(window: MainWindow) -> None:
    """Rebuild the Open Recent submenu from persisted recent files.

    Parameters
    ----------
    window : MainWindow
        The main window owning ``_recent_menu`` and ``_file_ctrl``.

    """
    # QMenu.clear() removes the actions but does not delete them — they are
    # parented to the window, so without deleteLater each rebuild leaks them.
    for act in window._recent_menu.actions():
        act.deleteLater()
    window._recent_menu.clear()
    recent = get_recent_files()
    if not recent:
        act = QAction("(No recent files)", window)
        act.setEnabled(False)
        window._recent_menu.addAction(act)
        return
    for path_str in recent:
        name = Path(path_str).name
        act = QAction(f"{name}  —  {path_str}", window)
        act.setToolTip(path_str)
        act.triggered.connect(lambda checked=False, p=path_str: window._file_ctrl.open_recent(p))
        window._recent_menu.addAction(act)


def _action(window: MainWindow, label: str, shortcut, slot) -> QAction:
    act = QAction(label, window)
    if shortcut is not None:
        act.setShortcut(shortcut)
    act.triggered.connect(slot)
    return act
