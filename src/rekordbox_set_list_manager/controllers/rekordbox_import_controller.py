"""Rekordbox import and re-match for downloaded tracks.

Closes the download loop: after :class:`TidalDownloadService` writes audio
files, this controller ingests them into the Rekordbox collection
(db-mode via :meth:`RekordboxDbService.import_files`, xml-mode via the
Rekordbox XML interchange path) and deterministically re-binds each
project track to its collection row — one undoable mutation for the whole
batch, with per-file skips and unmatched reports surfaced to the caller.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal

from rekordbox_set_list_manager.controllers.track_match import apply_download_match
from rekordbox_set_list_manager.models.enums import MatchStatus
from rekordbox_set_list_manager.models.track import Track
from rekordbox_set_list_manager.services import telemetry
from rekordbox_set_list_manager.services.rekordbox_db import (
    ImportItem,
    RekordboxDbError,
    RekordboxDbService,
)
from rekordbox_set_list_manager.services.skipped_item import SkippedItem
from rekordbox_set_list_manager.services.track_matcher import MatchStrategy
from rekordbox_set_list_manager.utils import config

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from uuid import UUID

    from rekordbox_set_list_manager.controllers.edit_controller import EditController
    from rekordbox_set_list_manager.controllers.project_controller import ProjectController
    from rekordbox_set_list_manager.models.project import Project
    from rekordbox_set_list_manager.services.rekordbox_xml import RekordboxXmlService
    from rekordbox_set_list_manager.services.tidal_download import DownloadResult

log = logging.getLogger(__name__)

_REREAD_ATTEMPTS = 3
_REREAD_DELAY_S = 0.5


class RekordboxImportError(Exception):
    """Raised when a download import cannot proceed or completes badly."""


@dataclass(frozen=True)
class ImportResult:
    """Aggregate outcome of one :meth:`RekordboxImportController.import_downloaded` call.

    Attributes
    ----------
    mode : str
        The import mode that ran (``"db"`` or ``"xml"``).
    inserted : int
        Number of new rows written to the Rekordbox database (db mode).
    matched : int
        Number of project tracks re-bound to a collection track.
    skipped : list[SkippedItem]
        Per-file skips (missing file, duplicate, no project track,
        manual-match protection) with reasons.
    unmatched : list[SkippedItem]
        Downloads that ingested but found no collection row to bind.
    needs_reload_tag : bool
        ``True`` when rows were inserted — Rekordbox must reload its
        collection (and may need to analyze the new files) before the
        tracks are usable.
    pending_import : bool
        ``True`` when the import produced an XML file the user still has
        to import into Rekordbox manually (xml mode).
    xml_path : Path | None
        Location of the generated Rekordbox XML file, or ``None`` in db
        mode.
    """

    mode: str
    inserted: int
    matched: int
    skipped: list[SkippedItem]
    unmatched: list[SkippedItem]
    needs_reload_tag: bool
    pending_import: bool
    xml_path: Path | None


class RekordboxImportController(QObject):
    """Ingest downloaded files into Rekordbox and re-match project tracks.

    Orchestrates the second half of the download loop: Rekordbox ingest,
    collection re-read, deterministic re-match (exact filepath → ISRC →
    inserted-row fallback — never the fuzzy matcher), and a single
    undoable mutation through :class:`EditController`.

    Signals
    -------
    import_finished : emitted with the :class:`ImportResult` once a call
        completes (including the empty-batch no-op); not emitted when the
        call raises.
    """

    import_finished = Signal(object)

    def __init__(
        self,
        ctrl: ProjectController,
        edit_ctrl: EditController,
        *,
        db_service: RekordboxDbService | None = None,
        xml_service: RekordboxXmlService | None = None,
        sleep: Callable[[float], None] = time.sleep,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._ctrl = ctrl
        self._edit_ctrl = edit_ctrl
        self._db = db_service if db_service is not None else RekordboxDbService()
        self._xml = xml_service  # Reserved for the Rekordbox XML import path.
        self._sleep = sleep

    # ------------------------------------------------------------------ API

    def import_downloaded(self, results: Iterable[DownloadResult]) -> ImportResult:
        """Ingest downloaded files into Rekordbox and re-match the project.

        Parameters
        ----------
        results : Iterable[DownloadResult]
            The files just delivered by the download service.

        Returns
        -------
        ImportResult
            Aggregate outcome — counts, per-file skips, unmatched rows,
            and the reload/pending-import flags the caller should surface.

        Raises
        ------
        RekordboxImportError
            If ``rekordbox_import_mode`` is unknown, no project is loaded,
            or the configured mode is not supported yet.

        """
        results = list(results)

        mode = str(config.get("rekordbox_import_mode", "db"))
        if mode not in {"db", "xml"}:
            raise RekordboxImportError(
                f"Unknown rekordbox_import_mode {mode!r} — expected 'db' or 'xml'"
            )

        project = self._ctrl.project
        if project is None:
            raise RekordboxImportError(
                "No project is loaded — open a project before importing downloads"
            )

        if not results:
            result = ImportResult(
                mode=mode,
                inserted=0,
                matched=0,
                skipped=[],
                unmatched=[],
                needs_reload_tag=False,
                pending_import=False,
                xml_path=None,
            )
            self.import_finished.emit(result)
            return result

        # Join downloads to project tracks on the Tidal id stamped at
        # download time — the only identity shared by both sides.
        project_tracks = {
            str(t.tidal_id): t for t in project.tracks.values() if t.tidal_id is not None
        }

        items = [
            self._to_import_item(result, project_tracks.get(str(result.track_id)))
            for result in results
        ]

        if mode == "xml":
            raise RekordboxImportError("rekordbox_import_mode 'xml' is not supported yet")

        inserted_ids, skipped = self._db.import_files(items)
        try:
            collection = self._reread_collection()
        except RekordboxDbError:
            # The committed rows are already in Rekordbox — a dead re-read
            # degrades to the authoritative insert IDs, it never fails the
            # write that already landed.
            log.warning(
                "Collection re-read failed after ingest; falling back to insert ids",
                exc_info=True,
            )
            collection = []

        resolved, unmatched = self._resolve_matches(
            results, items, project_tracks, inserted_ids, collection
        )

        def _apply(proj: Project) -> int:
            changed = 0
            for tid, (local, strategy) in resolved.items():
                track = proj.get_track(tid)
                if track is None:
                    continue
                apply_download_match(track, local, strategy)
                changed += 1
            return changed

        # One funnel call → one undo entry for the whole re-match batch,
        # no matter how many tracks the closure touches.
        matched = self._edit_ctrl.apply_mutation(_apply) or 0

        telemetry.record(
            "rekordbox_import",
            total=len(results),
            matched=matched,
            strategy_filepath=sum(1 for _, s in resolved.values() if s is MatchStrategy.FILEPATH),
            strategy_isrc=sum(1 for _, s in resolved.values() if s is MatchStrategy.ISRC),
        )

        result = ImportResult(
            mode=mode,
            inserted=len(inserted_ids),
            matched=matched,
            skipped=skipped,
            unmatched=unmatched,
            needs_reload_tag=mode == "db" and bool(inserted_ids),
            pending_import=False,
            xml_path=None,
        )
        self.import_finished.emit(result)
        return result

    # ------------------------------------------------------------------ internals

    @staticmethod
    def _to_import_item(result: DownloadResult, proj_track: Track | None) -> ImportItem:
        """Build an :class:`ImportItem`, preferring project metadata over the filename."""
        path = Path(result.filepath)
        return ImportItem(
            path=path,
            title=proj_track.title if proj_track is not None else path.stem,
            isrc=proj_track.isrc if proj_track is not None else None,
        )

    def _reread_collection(self) -> list[Track]:
        """Re-read the Rekordbox collection with a bounded locked-error retry.

        The database can still be locked while Rekordbox finishes releasing
        it (or while the just-committed write settles), so lock-shaped
        failures get a few short retries.  A locked failure on the last
        attempt — like any other :class:`RekordboxDbError` — re-raises to
        the caller, which degrades to the authoritative insert IDs.

        Raises
        ------
        RekordboxDbError
            On a non-lock failure, or when the database is still locked on
            the final attempt.

        """
        for attempt in range(_REREAD_ATTEMPTS):
            try:
                return list(self._db.get_collection())
            except RekordboxDbError as exc:
                if "locked" not in str(exc).lower() or attempt >= _REREAD_ATTEMPTS - 1:
                    raise
                self._sleep(_REREAD_DELAY_S)
        return []  # pragma: no cover — the loop always returns or raises

    def _resolve_matches(
        self,
        results: list[DownloadResult],
        items: list[ImportItem],
        project_tracks: dict[str, Track],
        inserted: dict[str, int],
        collection: list[Track],
    ) -> tuple[dict[UUID, tuple[Track, MatchStrategy]], list[SkippedItem]]:
        """Pair each download with a collection track via the deterministic chain.

        Order: exact filepath → ISRC (lowest Rekordbox ID wins on
        duplicates) → the row just inserted for this path.  Anything left
        is reported as unmatched rather than fuzzy-guessed — including
        downloads with no project track and tracks the user already bound
        by hand, which are never silently rebound.
        """
        by_path: dict[str, Track] = {}
        by_isrc: dict[str, list[Track]] = {}
        for local in collection:
            if local.filepath:
                by_path.setdefault(local.filepath, local)
            if local.isrc:
                by_isrc.setdefault(local.isrc, []).append(local)

        resolved: dict[UUID, tuple[Track, MatchStrategy]] = {}
        unmatched: list[SkippedItem] = []

        for result, item in zip(results, items, strict=True):
            path = Path(result.filepath)
            track = project_tracks.get(str(result.track_id))
            if track is None:
                unmatched.append(SkippedItem(path.name, "no project track with this Tidal ID"))
                continue
            if track.match_status is MatchStatus.MANUALLY_MATCHED:
                # Explicit user bindings win — reported, never rebound.
                unmatched.append(
                    SkippedItem(
                        track.display_name,
                        "already manually matched — kept existing binding",
                    )
                )
                continue

            local = by_path.get(str(path))
            strategy = MatchStrategy.FILEPATH
            if local is None and track.isrc and track.isrc in by_isrc:
                # A shared ISRC is ambiguous: the lowest Rekordbox ID wins
                # deterministically, regardless of collection ordering.
                local = min(by_isrc[track.isrc], key=_lowest_id_key)
                strategy = MatchStrategy.ISRC
            if local is None:
                inserted_id = inserted.get(str(path))
                if inserted_id is not None:
                    # The re-read missed a row we just committed — the
                    # insert's ID is authoritative for this path.
                    local = Track(
                        title=track.title,
                        artist=track.artist,
                        filepath=str(path),
                        isrc=item.isrc,
                        rekordbox_id=inserted_id,
                    )
            if local is None:
                unmatched.append(
                    SkippedItem(track.display_name, "not found in the Rekordbox collection")
                )
                continue
            # Keyed by track UUID: duplicate results for the same track
            # collapse deterministically to the last resolution.
            resolved[track.id] = (local, strategy)

        return resolved, unmatched


def _lowest_id_key(track: Track) -> tuple[bool, int, str]:
    """Sort key for lowest-Rekordbox-ID resolution; missing IDs sort last."""
    rid = track.rekordbox_id
    return (rid is None, rid if rid is not None else 0, track.filepath or "")
