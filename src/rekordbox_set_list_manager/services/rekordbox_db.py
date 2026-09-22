"""Read/write access to the local Rekordbox 6/7 database via pyrekordbox."""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING, Any

import platformdirs

from rekordbox_set_list_manager.models.enums import MatchStatus, RekordboxColor, TrackSource
from rekordbox_set_list_manager.models.track import Track
from rekordbox_set_list_manager.services.skipped_item import SkippedItem

if TYPE_CHECKING:
    from collections.abc import Iterable
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


@dataclass(frozen=True)
class ImportItem:
    """One downloaded file to ingest into the Rekordbox collection.

    Attributes
    ----------
    path : Path
        Absolute path of the audio file to add.
    title : str
        Title stamped on the new ``DjmdContent`` row — keeps the row visible
        to the collection read path before Rekordbox analysis fills it in.
    isrc : str | None
        Optional ISRC to stamp; enables the deterministic ISRC join.
    """

    path: Path
    title: str
    isrc: str | None = None


class RekordboxDbService:
    """Read/write wrapper around a local Rekordbox 6/7 SQLite database.

    Uses pyrekordbox to auto-detect and open the database using the Pioneer
    application settings on the local machine.  Reads the collection for
    matching; writes via ``export_set_to_db`` and ``import_files``, which
    refuse to run while Rekordbox is open and back up the database files
    first.
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
                    content = min(rows, key=_content_id_sort_key, default=None)
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
            If Rekordbox is running, the pre-write backup fails, or the
            database cannot be opened, written, or committed.

        """
        # Guard + backup run before the try so their RekordboxDbErrors reach
        # the caller unwrapped and unambiguous.
        self._prepare_write("exporting")

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

    def import_files(self, items: Iterable[ImportItem]) -> tuple[dict[str, int], list[SkippedItem]]:
        """Ingest downloaded audio files into the Rekordbox collection.

        Shared write pre-flight: refuses to run while Rekordbox is running,
        then backs up the database files before opening the database.  Each
        existing file is inserted via ``db.add_content`` (which flushes but
        does not commit) and the whole batch is committed in a single
        ``db.commit()`` call.

        Parameters
        ----------
        items : Iterable[ImportItem]
            Downloaded files to ingest, with the title and optional ISRC to
            stamp on each new ``DjmdContent`` row.

        Returns
        -------
        tuple[dict[str, int], list[SkippedItem]]
            Mapping of ``str(path)`` → inserted ``DjmdContent.ID`` for each
            row actually inserted, plus the per-file skips.

        Raises
        ------
        RekordboxDbError
            If Rekordbox is running, the running check itself fails, the
            pre-write backup fails, or the database cannot be opened,
            written, or committed.

        Notes
        -----
        - ``item.path`` values are converted to :class:`pathlib.Path` before
          ``add_content`` is consulted — accept ``str`` safely from the
          caller's ``DownloadResult.filepath``.
        - ``Title`` is always stamped (falling back to the file stem) so
          freshly inserted rows survive the collection read path, which
          drops rows with blank titles.
        - ``ISRC`` is only passed when present.
        - A per-file ``ValueError`` (e.g. duplicate ``FolderPath``) becomes a
          :class:`SkippedItem`; the batch continues.  Any other failure
          aborts the batch without committing partial results.

        """
        items = list(items)
        if not items:
            return {}, []

        # Guard + backup run before the try so their RekordboxDbErrors reach
        # the caller unwrapped and unambiguous.
        self._prepare_write("importing")

        db = None
        try:
            from pyrekordbox import Rekordbox6Database  # noqa: PLC0415

            inserted: dict[str, int] = {}
            skipped: list[SkippedItem] = []

            with Rekordbox6Database() as db:
                for item in items:
                    path = Path(item.path)
                    if not path.exists():
                        skipped.append(
                            SkippedItem(path.name, "file does not exist", path=str(path))
                        )
                        continue
                    try:
                        content = db.add_content(
                            path,
                            Title=item.title or path.stem,
                            **({"ISRC": item.isrc} if item.isrc else {}),
                        )
                    except ValueError as exc:
                        skipped.append(
                            SkippedItem(path.name, f"not imported: {exc}", path=str(path))
                        )
                        continue
                    inserted[str(path)] = int(content.ID)
                db.commit()
        except RekordboxDbError:
            raise
        except Exception as exc:
            raise RekordboxDbError(f"Failed to import files into Rekordbox: {exc}") from exc
        else:
            return inserted, skipped
        finally:
            if db is not None:
                db.engine.dispose()

    def _prepare_write(self, action: str) -> None:
        """Guard against a running Rekordbox and back up the DB files.

        Shared pre-flight for every write path that touches the Rekordbox
        database (export, import).  The guard always runs *before* the
        backup so a refused write cannot create a misleading timestamped
        backup row.

        Parameters
        ----------
        action : str
            Gerund naming the operation in progress (``"exporting"``,
            ``"importing"``) — used verbatim in the running-guard message.

        Raises
        ------
        RekordboxDbError
            If Rekordbox is running, if the running check itself fails
            (fail closed — writing without the guard is unsafe), or if the
            pre-write backup cannot be completed.
        """
        try:
            pid = get_rekordbox_pid()
        except Exception as exc:
            raise RekordboxDbError(f"Could not check whether Rekordbox is running: {exc}") from exc
        if pid:
            raise RekordboxDbError(f"Rekordbox is running — close it before {action}")

        # Snapshot the DB (and its WAL/SHM siblings) before opening so a
        # failed write can never leave the library unrecoverable.  When the
        # DB path cannot be detected the write still proceeds — pyrekordbox
        # may locate the database on its own — but loudly: a backup-less
        # write is exactly what this pre-flight exists to prevent.
        db_path = self.get_db_path()
        if db_path is None:
            log.warning(
                "Could not locate the Rekordbox DB path — proceeding without a "
                "pre-write backup"
            )
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
    """Snapshot the database file and its WAL/SHM siblings into the backup dir.

    Every sibling matching ``<db name>*`` in the database's directory is
    copied to ``<user data dir>/backups/rekordbox/<db stem>-<UTC ts>.*`` and
    locked down to owner-only ``0o600`` — a backup holds the entire DJ
    library, so it gets the same treatment as credential files.  The glob is
    derived from ``db_path.name`` rather than hardcoded to ``master.db*`` so
    the snapshot always covers the file actually being written (e.g. a
    renamed copy), never an unrelated sibling.  Afterwards only the newest
    ``_BACKUP_KEEP`` timestamp-groups for that stem are kept; older ones are
    deleted.

    Parameters
    ----------
    db_path : Path
        Path to the Rekordbox database file (normally ``master.db``).
    """
    backup_dir = (
        Path(platformdirs.user_data_dir("rekordbox_set_list_manager")) / "backups" / "rekordbox"
    )
    backup_dir.mkdir(parents=True, exist_ok=True)

    # Microseconds keep same-second exports from overwriting each other's
    # backup file set.
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    for src in sorted(db_path.parent.glob(f"{db_path.name}*")):
        dest = backup_dir / f"{db_path.stem}-{timestamp}{src.suffix}"
        shutil.copy2(src, dest)
        dest.chmod(0o600)

    # A backup is a file set: group by the timestamp prefix and drop whole
    # groups so a pruned set never loses its WAL/SHM siblings.
    groups: dict[str, list[Path]] = {}
    for file in backup_dir.glob(f"{db_path.stem}-*.db*"):
        groups.setdefault(file.name.split(".")[0], []).append(file)
    for key in sorted(groups)[:-_BACKUP_KEEP]:
        for file in groups[key]:
            file.unlink()


def _int_or_none(value: Any) -> int | None:  # noqa: ANN401
    """Return ``int(value)`` or ``None`` when it is not numeric."""
    try:
        return int(value) if value else None
    except TypeError, ValueError:
        return None


def _content_id_sort_key(row: Any) -> float:  # noqa: ANN401
    """Sort key for lowest-ID resolution; non-numeric IDs sort last."""
    parsed = _int_or_none(row.ID)
    return parsed if parsed is not None else float("inf")


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
    # DjmdContent.ID is a VARCHAR — a non-numeric value must not abort the
    # whole collection read; the track stays usable via its filepath.
    rekordbox_id = _int_or_none(content.ID)
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
