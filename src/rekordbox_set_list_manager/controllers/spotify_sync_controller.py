"""Spotify playlist order sync controller."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QMessageBox, QWidget

from rekordbox_set_list_manager.gui.widgets.common.busy_dialog import BusyDialog, show_error_dialog
from rekordbox_set_list_manager.services.spotify_service import SpotifyService, SpotifyServiceError

if TYPE_CHECKING:
    from rekordbox_set_list_manager.controllers.project_controller import ProjectController


class SpotifySyncController(QObject):
    """Pushes the current track order to the source Spotify playlist."""

    def __init__(
        self,
        ctrl: ProjectController,
        spotify: SpotifyService,
        parent_widget: QWidget,
        parent: QObject | None = None,
    ) -> None:
        """Initialise the Spotify sync controller with *ctrl* and *spotify*."""
        super().__init__(parent)
        self._ctrl = ctrl
        self._spotify = spotify
        self._w = parent_widget

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_order(self) -> None:
        """Push the current section track order to the linked Spotify playlist."""
        project = self._ctrl.project
        if project is None or not project.sections:
            QMessageBox.warning(self._w, "No project", "Open or create a project first.")
            return

        playlist_id = project.spotify_playlist_id
        if not playlist_id:
            QMessageBox.information(
                self._w,
                "No Spotify playlist",
                "This set list was not imported from Spotify.",
            )
            return

        spotify_uris: list[str] = []
        for track_id in project.all_track_ids:
            track = project.get_track(track_id)
            if track and track.spotify_id:
                spotify_uris.append(f"spotify:track:{track.spotify_id}")

        if not spotify_uris:
            QMessageBox.information(
                self._w,
                "No Spotify tracks",
                "No tracks in this set have Spotify IDs.",
            )
            return

        self._push(playlist_id, spotify_uris)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _push(self, playlist_id: str, spotify_uris: list[str]) -> None:
        """Ensure auth then push *spotify_uris* to *playlist_id* in a BusyDialog."""
        try:
            if self._spotify.try_silent_authenticate() is None:
                reply = QMessageBox.question(
                    self._w,
                    "Spotify authentication required",
                    "Your Spotify login has expired. Open browser to re-authenticate?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
                self._spotify.authenticate()
        except SpotifyServiceError as exc:
            QMessageBox.critical(self._w, "Spotify Error", str(exc))
            return

        dlg = BusyDialog(
            f"Updating Spotify playlist with {len(spotify_uris)} track(s)…",
            self._w,
            cancellable=False,
        )
        ok, _result, error = dlg.run(
            lambda: self._spotify.replace_playlist_tracks(playlist_id, spotify_uris)
        )
        if not ok:
            if error:
                self._handle_push_error(error)
            return

        QMessageBox.information(
            self._w,
            "Playlist updated",
            f"Spotify playlist updated with {len(spotify_uris)} track(s) in current order.",
        )

    def _handle_push_error(self, msg: str) -> None:
        if "403" in msg or "permission" in msg.lower():
            QMessageBox.warning(
                self._w,
                "Spotify permission needed",
                "Your Spotify login doesn't have playlist-modify permission.\n\n"
                "Re-import the playlist from Project → Import from Streaming "
                "Service to refresh your credentials with the new permissions, "
                "then try again.",
            )
        else:
            show_error_dialog(self._w, "Spotify Error", msg)
