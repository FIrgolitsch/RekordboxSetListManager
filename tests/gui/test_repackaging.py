"""Regression: all widget subpackages and controllers importable without error.

These tests guard against accidental breakage of module paths during
future refactors.
"""

from __future__ import annotations


def test_import_common_widgets():
    from rekordbox_set_list_manager.gui.widgets.common.busy_dialog import BusyDialog
    from rekordbox_set_list_manager.gui.widgets.common.color_picker import ColorPickerWidget
    from rekordbox_set_list_manager.gui.widgets.common.qthread_worker import QThreadWorker
    from rekordbox_set_list_manager.gui.widgets.common.track_table import TrackFilterProxyModel

    assert BusyDialog is not None
    assert ColorPickerWidget is not None
    assert QThreadWorker is not None
    assert TrackFilterProxyModel is not None


def test_import_dialog_widgets():
    from rekordbox_set_list_manager.gui.widgets.dialogs.add_track_dialog import AddTrackDialog
    from rekordbox_set_list_manager.gui.widgets.dialogs.export_dialog import ExportDialog
    from rekordbox_set_list_manager.gui.widgets.dialogs.fix_match_dialog import FixMatchDialog
    from rekordbox_set_list_manager.gui.widgets.dialogs.import_dialog import ImportDialog
    from rekordbox_set_list_manager.gui.widgets.dialogs.rematch_dialog import RematchDialog
    from rekordbox_set_list_manager.gui.widgets.dialogs.section_edit_dialog import SectionEditDialog
    from rekordbox_set_list_manager.gui.widgets.dialogs.settings_dialog import SettingsDialog
    from rekordbox_set_list_manager.gui.widgets.dialogs.theme_dialog import ThemeDialog

    assert AddTrackDialog is not None
    assert ExportDialog is not None
    assert FixMatchDialog is not None
    assert ImportDialog is not None
    assert RematchDialog is not None
    assert SectionEditDialog is not None
    assert SettingsDialog is not None
    assert ThemeDialog is not None


def test_import_section_widgets():
    from rekordbox_set_list_manager.gui.widgets.sections.multi_section_view import MultiSectionView
    from rekordbox_set_list_manager.gui.widgets.sections.section_block import SectionBlock
    from rekordbox_set_list_manager.gui.widgets.sections.section_table_view import SectionTableView

    assert MultiSectionView is not None
    assert SectionBlock is not None
    assert SectionTableView is not None


def test_import_panel_widgets():
    from rekordbox_set_list_manager.gui.widgets.panels.match_info_widget import MatchInfoWidget
    from rekordbox_set_list_manager.gui.widgets.panels.transition_note import TransitionNoteWidget

    assert MatchInfoWidget is not None
    assert TransitionNoteWidget is not None


def test_import_streaming_widgets():
    from rekordbox_set_list_manager.gui.widgets.streaming.auth_worker import StreamingAuthWorker
    from rekordbox_set_list_manager.gui.widgets.streaming.collection_browser import (
        CollectionBrowserWidget,
    )
    from rekordbox_set_list_manager.gui.widgets.streaming.service_tab import StreamingServiceTab

    assert StreamingAuthWorker is not None
    assert CollectionBrowserWidget is not None
    assert StreamingServiceTab is not None


def test_import_controllers():
    from rekordbox_set_list_manager.controllers.edit_controller import EditController
    from rekordbox_set_list_manager.controllers.file_controller import FileController
    from rekordbox_set_list_manager.controllers.import_controller import ImportController
    from rekordbox_set_list_manager.controllers.project_controller import ProjectController
    from rekordbox_set_list_manager.controllers.rematch_controller import RematchController
    from rekordbox_set_list_manager.controllers.spotify_sync_controller import SpotifySyncController

    assert EditController is not None
    assert FileController is not None
    assert ImportController is not None
    assert ProjectController is not None
    assert RematchController is not None
    assert SpotifySyncController is not None


def test_import_extracted_controllers():
    from rekordbox_set_list_manager.controllers.autosave_controller import AutosaveController
    from rekordbox_set_list_manager.controllers.project_actions_controller import (
        ProjectActionsController,
    )
    from rekordbox_set_list_manager.controllers.track_controller import TrackController

    assert TrackController is not None
    for slot in ("add_track", "add_track_from_rekordbox", "fix_match", "match_fixed"):
        assert hasattr(TrackController, slot)
    assert ProjectActionsController is not None
    for slot in (
        "rename_project",
        "export",
        "export_setlist",
        "open_theme_dialog",
        "open_settings",
        "recolor_by_type",
    ):
        assert hasattr(ProjectActionsController, slot)
    assert AutosaveController is not None


def test_import_gui_helpers():
    from rekordbox_set_list_manager.gui.delegates.indicator_delegate import IndicatorDelegate
    from rekordbox_set_list_manager.gui.menu_bar import build_menus, update_recent_menu
    from rekordbox_set_list_manager.gui.widgets.dialogs.setlist_export_dialog import (
        SetlistExportDialog,
    )
    from rekordbox_set_list_manager.gui.widgets.panels.project_status_bar import ProjectStatusBar

    assert build_menus is not None
    assert update_recent_menu is not None
    assert ProjectStatusBar is not None
    assert hasattr(ProjectStatusBar, "update_for")
    assert IndicatorDelegate is not None
    assert SetlistExportDialog is not None


def test_import_harmonic_engine():
    from rekordbox_set_list_manager.models import KeyRelation
    from rekordbox_set_list_manager.models.harmonic import (
        ParsedKey,
        format_key,
        key_relationship,
        parse_key,
    )

    assert parse_key is not None
    assert key_relationship is not None
    assert format_key is not None
    assert ParsedKey is not None
    assert KeyRelation is not None


def test_import_setlist_export():
    from rekordbox_set_list_manager.services.setlist_export import (
        SetlistExportError,
        SetlistExportResult,
        export_setlist,
    )
    from rekordbox_set_list_manager.utils.formatting import format_total_duration

    assert export_setlist is not None
    assert SetlistExportError is not None
    assert SetlistExportResult is not None
    assert format_total_duration is not None
