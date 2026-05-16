"""Read-only access to the local Rekordbox 6/7 database via pyrekordbox."""

from __future__ import annotations

import logging
from pathlib import Path, PurePosixPath
from typing import Any

from rekordbox_set_list_manager.models.enums import MatchStatus, RekordboxColor, TrackSource
from rekordbox_set_list_manager.models.track import Track

log = logging.getLogger(__name__)

# Map DjmdColor.Commnt values (case-insensitive) to RekordboxColor
_COLOR_NAME_MAP: dict[str, RekordboxColor] = {
    "pink": RekordboxColor.PINK,
    "red": RekordboxColor.RED,
    "orange": RekordboxColor.ORANGE,
    "yellow": RekordboxColor.YELLOW,
    "green": RekordboxColor.GREEN,
    "aqua": RekordboxColor.AQUA,
    "cyan": RekordboxColor.AQUA,
    "blue": RekordboxColor.BLUE,
    "purple": RekordboxColor.PURPLE,
}


class RekordboxDbError(Exception):
    """Raised when the Rekordbox database cannot be opened or queried."""


class RekordboxDbService:
    """Read-only wrapper around a local Rekordbox 6/7 SQLite database.

    Uses pyrekordbox to auto-detect and open the database using the Pioneer
    application settings on the local machine.
    """

    def is_available(self) -> bool:
        """Return True if a Rekordbox database can be located and opened."""
        try:
            from pyrekordbox import Rekordbox6Database  # noqa: PLC0415

            with Rekordbox6Database():
                pass
        except Exception:  # noqa: BLE001
            return False
        else:
            return True

    def get_db_path(self) -> Path | None:
        """Return the Rekordbox database path without opening it, or ``None``."""
        try:
            from pyrekordbox.config import (  # noqa: PLC0415
                _get_rb6_config,  # type: ignore[attr-defined]
                _get_rb7_config,  # type: ignore[attr-defined]
                get_pioneer_app_dir,
                get_pioneer_install_dir,
            )

            prog = get_pioneer_install_dir()
            app = get_pioneer_app_dir()
            for fn in (_get_rb7_config, _get_rb6_config):
                cfg = fn(prog, app)
                p = cfg.get("db_path")
                if p is not None and Path(str(p)).exists():
                    return Path(str(p))
        except Exception:  # noqa: BLE001
            log.debug("Could not determine Rekordbox DB path", exc_info=True)
        return None

    def get_collection(self) -> list[Track]:
        """Load all tracks from the Rekordbox database as Track objects.

        Returns
        -------
        list[Track]
            All local (non-streaming) tracks found in the database.

        Raises
        ------
        RekordboxDbError
            If the database cannot be opened or read.

        """
        try:
            from pyrekordbox import Rekordbox6Database  # noqa: PLC0415

            tracks: list[Track] = []
            with Rekordbox6Database() as db:
                for content in db.get_content().all():
                    track = _content_to_track(content)
                    if track is not None:
                        tracks.append(track)
        except RekordboxDbError:
            raise
        except Exception as exc:
            raise RekordboxDbError(f"Failed to read Rekordbox database: {exc}") from exc
        else:
            return tracks

    def find_track_by_isrc(self, isrc: str) -> Track | None:
        """Return the first track with the given ISRC, or None.

        Parameters
        ----------
        isrc : str
            The ISRC code to search for.

        Returns
        -------
        Track | None
            The matching track, or ``None`` if not found.

        Raises
        ------
        RekordboxDbError
            If the database cannot be queried.

        """
        try:
            from pyrekordbox import Rekordbox6Database  # noqa: PLC0415

            with Rekordbox6Database() as db:
                content = db.get_content(ISRC=isrc).first()
                return _content_to_track(content) if content is not None else None
        except RekordboxDbError:
            raise
        except Exception as exc:
            raise RekordboxDbError(f"Failed to search Rekordbox database: {exc}") from exc

    def find_track_by_path(self, path: str) -> Track | None:
        """Return the track whose FolderPath matches *path*, or None.

        Parameters
        ----------
        path : str
            The file path string to match against the ``FolderPath`` column.

        Returns
        -------
        Track | None
            The matching track, or ``None`` if not found.

        Raises
        ------
        RekordboxDbError
            If the database cannot be queried.

        """
        try:
            from pyrekordbox import Rekordbox6Database  # noqa: PLC0415

            with Rekordbox6Database() as db:
                content = db.get_content(FolderPath=path).first()
                return _content_to_track(content) if content is not None else None
        except RekordboxDbError:
            raise
        except Exception as exc:
            raise RekordboxDbError(f"Failed to search Rekordbox database: {exc}") from exc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _content_to_track(content: Any) -> Track | None:  # noqa: ANN401
    """Convert a DjmdContent ORM row to a Track; return None if unusable."""
    title = content.Title
    if not title:
        return None

    # Skip tracks synced from streaming services (Spotify, Tidal, etc.).
    # ServiceID holds the streaming service type (non-zero when set);
    # SrcID holds the source track ID within that service.
    # Either field being set is sufficient to identify a streaming track.
    if content.ServiceID or content.SrcID:
        return None

    filepath = content.FolderPath or None
    if not filepath or not PurePosixPath(filepath).is_absolute():
        return None

    artist = content.ArtistName or ""
    bpm = (content.BPM / 100.0) if content.BPM else None
    key = content.KeyName or None
    duration = content.Length  # seconds (int or None)
    isrc = content.ISRC or None
    rekordbox_id = int(content.ID) if content.ID else None
    color = _parse_color(content.ColorName)

    return Track(
        title=title,
        artist=artist,
        bpm=bpm,
        key=key,
        duration=duration,
        isrc=isrc,
        source=TrackSource.REKORDBOX,
        rekordbox_id=rekordbox_id,
        filepath=filepath,
        match_status=MatchStatus.UNMATCHED,
        color=color,
    )


def _parse_color(color_name: str | None) -> RekordboxColor | None:
    """Map a Rekordbox color name string to a RekordboxColor enum value."""
    if not color_name:
        return None
    return _COLOR_NAME_MAP.get(color_name.casefold())
