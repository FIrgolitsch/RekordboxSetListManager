"""Read/write access to the local Rekordbox 6/7 database via pyrekordbox."""

from __future__ import annotations

import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING, Any

import platformdirs

from rekordbox_set_list_manager.models.enums import MatchStatus, RekordboxColor, TrackSource
from rekordbox_set_list_manager.models.track import Track
from rekordbox_set_list_manager.services.skipped_item import SkippedItem

if TYPE_CHECKING:
    from uuid import UUID

    from rekordbox_set_list_manager.models.section import Section

log = logging.getLogger(__name__)

# Number of pre-export backup timestamp-groups kept under
# ``<user data dir>/backups/rekordbox/``; older groups are pruned.
_BACKUP_KEEP = 5

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
    """Read/write wrapper around a local Rekordbox 6/7 SQLite database.

    Uses pyrekordbox to auto-detect and open the database using the Pioneer
    application settings on the local machine.  Reads the collection for
    matching; writes via ``export_set_to_db``, which refuses to run while
    Rekordbox is open and backs up the database files first.
    """

    def is_available(self) -> bool:
        """Return True if a Rekordbox database can be located and opened."""
        try:
            from pyrekordbox import Rekordbox6Database  # noqa: PLC0415

            with Rekordbox6Database():
                pass
        except Exception:
            # Best-effort probe: any failure (missing DB, driver, config)
            # means "not available" — callers only need the boolean.
            log.debug("Rekordbox database not available", exc_info=True)
            return False
        else:
            return True

    def get_db_path(self) -> Path | None:
        """Return the Rekordbox database path without opening it, or ``None``."""
        try:
            from rekordbox_set_list_manager.services import (  # noqa: PLC0415
                _pyrekordbox_adapter,
            )

            candidates = _pyrekordbox_adapter.rekordbox_db_candidates()
        except Exception:
            # Best-effort detection path: a broken pyrekordbox config means
            # "no known DB path", never a raised error.
            log.debug("Could not determine Rekordbox DB path", exc_info=True)
            return None
        return candidates[0] if candidates else None

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
                content = db.get_content(ISRC=isrc)
                if hasattr(content, "all"):
                    rows = content.all()
                    if len(rows) > 1:
                        # Ambiguous ISRC: pick deterministically (lowest
                        # content ID) and note it instead of silently
                        # collapsing to whatever .first() returned.
                        log.debug(
                            "ISRC %s matched %d Rekordbox rows; using the lowest ID",
                            isrc,
                            len(rows),
                        )
                    content = min(rows, key=lambda row: int(row.ID), default=None)
                elif hasattr(content, "first"):
                    content = content.first()
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
                content = db.get_content(FolderPath=path)
                if hasattr(content, "first"):
                    content = content.first()
                return _content_to_track(content) if content is not None else None
        except RekordboxDbError:
            raise
        except Exception as exc:
            raise RekordboxDbError(f"Failed to search Rekordbox database: {exc}") from exc

    def export_set_to_db(
        self,
        sections: list[Section],
        name: str,
        tracks: dict[UUID, Track],
    ) -> tuple[int, list[SkippedItem]]:
        """Write *sections* as a playlist folder directly to the Rekordbox database.

        Creates a top-level folder named *name* containing one playlist per
        section.  If a folder with that name already exists at the root level
        it is deleted and re-created so the export is idempotent.

        Only tracks that have a ``rekordbox_id`` (integer DB primary key) or a
        ``filepath`` that can be resolved to a ``DjmdContent`` row are written.
        Unmatched or streaming-only tracks are returned as ``SkippedItem``
        entries so the caller can show exactly what was left out and why.

        Parameters
        ----------
        sections : list[Section]
            Ordered sections whose tracks will be exported.
        name : str
            Name of the playlist folder created at the root of the
            Rekordbox database.
        tracks : dict[UUID, Track]
            Mapping of all available tracks by their UUID.

        Returns
        -------
        tuple[int, list[SkippedItem]]
            ``(exported_count, skipped_items)`` — how many track-playlist
            entries were written and an itemized list of the tracks that were
            skipped with the reason for each.

        Raises
        ------
        RekordboxDbError
            If Rekordbox is running, the pre-export backup fails, or the
            database cannot be opened, written, or committed.

        """
        # Guard + backup run before the try so their RekordboxDbErrors reach
        # the caller unwrapped and unambiguous.
        self._prepare_export()

        try:
            from pyrekordbox import Rekordbox6Database  # noqa: PLC0415

            exported = 0
            skipped: list[SkippedItem] = []

            with Rekordbox6Database() as db:
                # Remove any existing root-level playlist or folder with the
                # same name so the export is idempotent (re-running replaces
                # the old data). "Root-level" means the ParentID does not
                # reference another playlist row — covering every sentinel
                # Rekordbox/pyrekordbox uses ("root", None, "", "0", …).
                # delete_playlist removes a folder's children recursively.
                known_ids = {str(pl.ID) for pl in db.get_playlist().all()}
                existing = db.get_playlist(Name=name).all()
                for pl in existing:
                    if str(pl.ParentID) not in known_ids:
                        db.delete_playlist(pl)

                folder = db.create_playlist_folder(name)

                for section in sections:
                    playlist = db.create_playlist(section.name, parent=folder)
                    for tid in section.track_ids:
                        track = tracks.get(tid)
                        if track is None:
                            skipped.append(SkippedItem(str(tid), "not in project tracks"))
                            continue

                        content = None
                        if track.rekordbox_id is not None:
                            result = db.get_content(ID=track.rekordbox_id)
                            content = result.first() if hasattr(result, "first") else result
                        if content is None and track.filepath:
                            result = db.get_content(FolderPath=track.filepath)
                            content = result.first() if hasattr(result, "first") else result

                        if content is None:
                            skipped.append(
                                SkippedItem(
                                    track.display_name,
                                    "not found in Rekordbox collection",
                                )
                            )
                            continue

                        db.add_to_playlist(playlist, content)
                        exported += 1

                db.commit()
        except RekordboxDbError:
            raise
        except Exception as exc:
            raise RekordboxDbError(f"Failed to export to Rekordbox database: {exc}") from exc
        else:
            return exported, skipped

    def _prepare_export(self) -> None:
        """Guard against a running Rekordbox and back up the DB files.

        Raises
        ------
        RekordboxDbError
            If Rekordbox is running, if the running check itself fails
            (fail closed — exporting without the guard is unsafe), or if the
            pre-export backup cannot be completed.
        """
        try:
            pid = get_rekordbox_pid()
        except Exception as exc:
            raise RekordboxDbError(f"Could not check whether Rekordbox is running: {exc}") from exc
        if pid:
            raise RekordboxDbError("Rekordbox is running — close it before exporting")

        # Snapshot master.db (and its WAL/SHM siblings) before opening so a
        # failed export can never leave the library unrecoverable.
        db_path = self.get_db_path()
        if db_path is None:
            return
        try:
            _backup_db_files(db_path)
        except Exception as exc:
            raise RekordboxDbError(f"Could not back up Rekordbox database: {exc}") from exc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def get_rekordbox_pid() -> int:
    """Return the Rekordbox process ID, or 0 when it is not running.

    Thin wrapper around ``pyrekordbox.utils.get_rekordbox_pid`` so the heavy
    pyrekordbox package import stays deferred to call time.  pyrekordbox
    matches the process name exactly (``.exe`` stripped), so the always-on
    ``rekordboxAgent`` background process does not count.
    """
    from pyrekordbox import utils  # noqa: PLC0415

    return utils.get_rekordbox_pid()


def _backup_db_files(db_path: Path) -> None:
    """Snapshot ``master.db`` and its WAL/SHM siblings into the backup dir.

    Every sibling matching ``master.db*`` in the database's directory is
    copied to ``<user data dir>/backups/rekordbox/master-<UTC ts>.db*`` with
    permissions preserved.  Afterwards only the newest ``_BACKUP_KEEP``
    timestamp-groups are kept; older ones are deleted.

    Parameters
    ----------
    db_path : Path
        Path to the Rekordbox ``master.db`` file.
    """
    backup_dir = (
        Path(platformdirs.user_data_dir("rekordbox_set_list_manager")) / "backups" / "rekordbox"
    )
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    for src in sorted(db_path.parent.glob("master.db*")):
        shutil.copy2(src, backup_dir / f"master-{timestamp}{src.suffix}")

    # A backup is a file set: group by the timestamp prefix and drop whole
    # groups so a pruned set never loses its WAL/SHM siblings.
    groups: dict[str, list[Path]] = {}
    for file in backup_dir.glob("master-*.db*"):
        groups.setdefault(file.name.split(".")[0], []).append(file)
    for key in sorted(groups)[:-_BACKUP_KEEP]:
        for file in groups[key]:
            file.unlink()


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
    if not filepath or not (
        # Rekordbox on Windows stores drive-absolute (C:\...) or UNC paths;
        # drive-relative "C:foo" is correctly not absolute under either flavor.
        PurePosixPath(filepath).is_absolute() or PureWindowsPath(filepath).is_absolute()
    ):
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
