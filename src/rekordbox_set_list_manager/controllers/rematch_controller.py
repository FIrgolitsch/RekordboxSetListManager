"""Rekordbox re-match controller."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget

from rekordbox_set_list_manager.controllers.track_match import apply_track_match
from rekordbox_set_list_manager.gui.widgets.common.busy_dialog import BusyDialog
from rekordbox_set_list_manager.models.enums import MatchStatus
from rekordbox_set_list_manager.services import telemetry
from rekordbox_set_list_manager.services.rekordbox_db import RekordboxDbService
from rekordbox_set_list_manager.services.rekordbox_xml import RekordboxXmlService
from rekordbox_set_list_manager.services.track_matcher import TrackMatcher

if TYPE_CHECKING:
    from rekordbox_set_list_manager.controllers.edit_controller import EditController
    from rekordbox_set_list_manager.controllers.project_controller import ProjectController
    from rekordbox_set_list_manager.gui.widgets.sections.multi_section_view import MultiSectionView
    from rekordbox_set_list_manager.models.track import Track


class RematchController(QObject):
    """Handles re-matching project tracks against a Rekordbox collection."""

    def __init__(
        self,
        ctrl: ProjectController,
        edit_ctrl: EditController,
        view: MultiSectionView,
        parent_widget: QWidget,
        parent: QObject | None = None,
    ) -> None:
        """Initialise the rematch controller with project and UI dependencies."""
        super().__init__(parent)
        self._ctrl = ctrl
        self._edit_ctrl = edit_ctrl
        self._view = view
        self._w = parent_widget

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def rematch_xml(self) -> None:
        """Prompt for a Rekordbox XML file and re-match all project tracks against it."""
        if self._ctrl.project is None:
            QMessageBox.warning(self._w, "No project", "Open or create a project first.")
            return
        path_str, _ = QFileDialog.getOpenFileName(
            self._w, "Open Rekordbox XML", "", "XML files (*.xml)"
        )
        if not path_str:
            return
        dlg = BusyDialog("Loading Rekordbox collection\u2026", self._w, cancellable=True)
        ok, collection, error = dlg.run(
            lambda: RekordboxXmlService().import_collection(Path(path_str))
        )
        if not ok:
            if error:
                QMessageBox.critical(self._w, "Rekordbox Error", error)
            return
        self._do_rematch(collection)

    def rematch_db(self) -> None:
        """Re-match all project tracks against the live Rekordbox database."""
        if self._ctrl.project is None:
            QMessageBox.warning(self._w, "No project", "Open or create a project first.")
            return
        dlg = BusyDialog("Loading Rekordbox collection\u2026", self._w, cancellable=True)
        ok, collection, error = dlg.run(RekordboxDbService().get_collection)
        if not ok:
            if error:
                QMessageBox.critical(self._w, "Rekordbox DB Error", error)
            return
        self._do_rematch(collection)

    def rematch_manual(self) -> None:
        """Open the manual re-match dialog and apply any user-confirmed matches."""
        from rekordbox_set_list_manager.gui.widgets.dialogs.rematch_dialog import (  # noqa: PLC0415
            RematchDialog,
        )

        project = self._ctrl.project
        if project is None:
            QMessageBox.warning(self._w, "No project", "Open or create a project first.")
            return
        dialog = RematchDialog(project, self._w)
        if dialog.exec() != RematchDialog.DialogCode.Accepted:
            return
        pending = dialog.pending_matches()
        if not pending:
            return
        self._edit_ctrl.push_snapshot()
        changed = 0
        for track_id, local_track in pending.items():
            track = project.get_track(track_id)
            if track is None:
                continue
            apply_track_match(track, local_track)
            changed += 1
        self._view.refresh_tracks(project.tracks)
        self._edit_ctrl.notify_changed()
        QMessageBox.information(self._w, "Manual re-match complete", f"{changed} track(s) updated.")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _do_rematch(self, collection: list[Track]) -> None:
        project = self._ctrl.project
        if project is None:
            return
        all_tracks = list(project.tracks.values())
        results = TrackMatcher().match(all_tracks, collection)
        self._edit_ctrl.push_snapshot()
        newly_matched = 0
        strategy_counts: dict[str, int] = {}
        for result in results:
            if result.local_track is not None:
                track = project.tracks.get(result.spotify_track.id)
                if track is not None and track.match_status == MatchStatus.MANUALLY_MATCHED:
                    continue
                project.tracks[result.spotify_track.id] = result.local_track
                newly_matched += 1
                strat = result.local_track.match_strategy or "none"
                strategy_counts[strat] = strategy_counts.get(strat, 0) + 1
        self._view.refresh_tracks(project.tracks)
        self._edit_ctrl.notify_changed()
        telemetry.record(
            "match_strategy_used",
            total=len(all_tracks),
            matched=newly_matched,
            **{f"strategy_{k}": v for k, v in strategy_counts.items()},
        )
        QMessageBox.information(
            self._w,
            "Re-match complete",
            f"{newly_matched} of {len(all_tracks)} track(s) matched to local files.",
        )
