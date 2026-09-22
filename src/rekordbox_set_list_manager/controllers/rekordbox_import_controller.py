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
from rekordbox_set_list_manager.services.rekordbox_xml import RekordboxXmlService
from rekordbox_set_list_manager.services.skipped_item import SkippedItem
from rekordbox_set_list_manager.services.track_matcher import MatchStrategy
from rekordbox_set_list_manager.utils import config

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from uuid import UUID

    from rekordbox_set_list_manager.controllers.edit_controller import EditController
    from rekordbox_set_list_manager.controllers.project_controller import ProjectController
    from rekordbox_set_list_manager.models.project import Project
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
        ``True`` when xml-mode items were written to the manifest but not
        yet found in the collection — the user still has to run
        Rekordbox's rekordbox-xml bridge import.
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

    Threading
    ---------
    :meth:`import_downloaded` must be called on the GUI thread.  It
    performs synchronous Rekordbox I/O plus bounded re-read sleeps (so it
    briefly blocks) and then mutates ``Project``/``UndoStack`` in place —
    neither is safe from a worker thread, and the injectable ``sleep``
    exists for tests, not as a threading seam.  If ingest is ever moved to
    a worker, only the ingest may run there; the ``apply_mutation`` call
    must be marshalled back to the GUI thread.

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
        # Instantiated lazily on the first xml-mode run when not injected.
        self._xml = xml_service
        self._sleep = sleep

    # ------------------------------------------------------------------ API

    def import_downloaded(self, results: Iterable[DownloadResult]) -> ImportResult:
        """Ingest downloaded files into Rekordbox and re-match the project.

        Must be called on the GUI thread — see the class-level
        ``Threading`` contract.

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
            or xml mode is configured without ``download_dir``.

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

        xml_path: Path | None = None
        inserted_ids: dict[str, int] = {}
        if mode == "xml":
            xml_path, skipped = self._write_import_xml(results, project_tracks)
        else:
            inserted_ids, skipped = self._db.import_files(items)

        try:
            collection = self._reread_collection()
        except RekordboxDbError:
            # db mode: the committed rows are already in Rekordbox — a dead
            # re-read degrades to the authoritative insert IDs, it never
            # fails the write that already landed.  xml mode: a dead re-read
            # simply finds nothing, so every unresolved item reports pending
            # rather than misreporting a written manifest as a failure.
            log.warning(
                "Collection re-read failed after import; degrading to %s",
                "insert ids" if mode == "db" else "pending-import reporting",
                exc_info=True,
            )
            collection = []

        resolved, unmatched, pending = self._resolve_matches(
            results, items, project_tracks, inserted_ids, collection, skipped, mode
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
            pending_import=mode == "xml" and pending > 0,
            xml_path=xml_path,
        )
        self.import_finished.emit(result)
        return result

    # ------------------------------------------------------------------ internals

    def _write_import_xml(
        self,
        results: list[DownloadResult],
        project_tracks: dict[str, Track],
    ) -> tuple[Path, list[SkippedItem]]:
        """Write the collection-only ingest manifest for xml mode.

        One ``TRACK`` element per downloaded file that exists on disk;
        missing files are reported as skips and omitted so the manifest
        never carries a dead ``Location``.  The file lands at the fixed
        ``rekordbox-import.xml`` path under ``download_dir`` — the path the
        user binds once in Rekordbox's rekordbox-xml bridge preferences.

        This mode never opens the database for writing: the running-guard
        and backup pre-flight protect DB writes and are deliberately not
        invoked — xml mode expressly works while Rekordbox is open.

        Parameters
        ----------
        results : list[DownloadResult]
            The delivered downloads to manifest.
        project_tracks : dict[str, Track]
            Project tracks keyed by Tidal ID, consulted for the title,
            artist, and analysis metadata carried into each ``TRACK``.

        Returns
        -------
        tuple[Path, list[SkippedItem]]
            The manifest path written and the per-file skips.

        Raises
        ------
        RekordboxImportError
            If the ``download_dir`` config key is not set.

        """
        raw_dir = config.get("download_dir")
        if not raw_dir:
            raise RekordboxImportError(
                "rekordbox_import_mode 'xml' requires the 'download_dir' config key to be set"
            )
        # expanduser: a hand-edited config.json commonly writes "~" —
        # pathlib leaves it literal, which would write the manifest into a
        # stray "~" directory instead of the bound download folder.
        xml_path = Path(str(raw_dir)).expanduser() / "rekordbox-import.xml"

        xml_tracks: list[Track] = []
        skipped: list[SkippedItem] = []
        for result in results:
            path = Path(result.filepath)
            if not path.exists():
                skipped.append(SkippedItem(path.name, "file does not exist", path=str(path)))
                continue
            track = project_tracks.get(str(result.track_id))
            xml_tracks.append(
                Track(
                    title=track.title if track is not None else path.stem,
                    artist=track.artist if track is not None else "",
                    isrc=track.isrc if track is not None else None,
                    bpm=track.bpm if track is not None else None,
                    key=track.key if track is not None else None,
                    duration=track.duration if track is not None else None,
                    filepath=str(path),
                )
            )

        if self._xml is None:
            self._xml = RekordboxXmlService()
        self._xml.export_collection(xml_tracks, xml_path)
        return xml_path, skipped

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
        skipped: list[SkippedItem],
        mode: str,
    ) -> tuple[dict[UUID, tuple[Track, MatchStrategy]], list[SkippedItem], int]:
        """Pair each download with a collection track via the deterministic chain.

        Order: exact filepath → ISRC (lowest Rekordbox ID wins on
        duplicates) → the row just inserted for this path.  Anything left
        is reported as unmatched rather than fuzzy-guessed — including
        downloads with no project track and tracks the user already bound
        by hand, which are never silently rebound.  The returned pending
        count tracks unresolved items in xml mode, where a missing row is
        pending the user's bridge import rather than lost.

        Downloads already rejected by ingest are reported once in
        ``skipped`` and are never re-reported here — so ``skipped`` and
        ``unmatched`` partition the batch, and an absent-from-manifest file
        is never counted as a pending bridge import.  A skipped download
        can still resolve when a collection row already exists for its
        path (the dedup-skip case: the file was refused because the row is
        there), so the skip check gates reporting, not resolution.
        """
        by_path: dict[str, Track] = {}
        by_isrc: dict[str, list[Track]] = {}
        for local in collection:
            if local.filepath:
                by_path.setdefault(local.filepath, local)
            if local.isrc:
                by_isrc.setdefault(local.isrc, []).append(local)

        skipped_paths = {s.path for s in skipped if s.path is not None}

        resolved: dict[UUID, tuple[Track, MatchStrategy]] = {}
        unmatched: list[SkippedItem] = []
        pending = 0

        for result, item in zip(results, items, strict=True):
            path = Path(result.filepath)
            ingest_skipped = str(path) in skipped_paths
            track = project_tracks.get(str(result.track_id))
            if track is None:
                if not ingest_skipped:
                    unmatched.append(
                        SkippedItem(path.name, "no project track with this Tidal ID")
                    )
                continue
            if track.match_status is MatchStatus.MANUALLY_MATCHED:
                # Explicit user bindings win — reported, never rebound.
                if not ingest_skipped:
                    unmatched.append(
                        SkippedItem(
                            track.display_name,
                            "already manually matched — kept existing binding",
                        )
                    )
                continue

            local, strategy = _collection_match(path, track, item, by_path, by_isrc, inserted)
            if local is None:
                if not ingest_skipped:
                    report, pending_bump = _unresolved_report(track, mode)
                    unmatched.append(report)
                    pending += pending_bump
                continue
            # Keyed by track UUID: duplicate results for the same track
            # collapse deterministically to the last resolution.
            resolved[track.id] = (local, strategy)

        return resolved, unmatched, pending


def _collection_match(
    path: Path,
    track: Track,
    item: ImportItem,
    by_path: dict[str, Track],
    by_isrc: dict[str, list[Track]],
    inserted: dict[str, int],
) -> tuple[Track | None, MatchStrategy]:
    """Resolve one download to a collection track via the deterministic chain.

    Order: exact filepath → shared ISRC → the row just inserted for this
    path.  Returns ``(None, FILEPATH)`` when nothing resolves.
    """
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
            # The re-read missed a row we just committed — the insert's ID
            # is authoritative for this path.
            local = Track(
                title=track.title,
                artist=track.artist,
                filepath=str(path),
                isrc=item.isrc,
                rekordbox_id=inserted_id,
            )
    return local, strategy


def _unresolved_report(track: Track, mode: str) -> tuple[SkippedItem, int]:
    """Build the report for a download that resolved to no collection row.

    Returns the :class:`SkippedItem` plus the pending increment: 1 in xml
    mode (the row is pending the user's bridge import), else 0.
    """
    if mode == "xml":
        return (
            SkippedItem(
                track.display_name,
                "not yet in the Rekordbox collection — import "
                "rekordbox-import.xml via Rekordbox's rekordbox xml "
                "bridge (Preferences → Advanced → rekordbox xml → "
                "Imported Library → Import To Collection), then re-run",
            ),
            1,
        )
    return (
        SkippedItem(track.display_name, "not found in the Rekordbox collection"),
        0,
    )


def _lowest_id_key(track: Track) -> tuple[bool, int, str]:
    """Sort key for lowest-Rekordbox-ID resolution; missing IDs sort last."""
    rid = track.rekordbox_id
    return (rid is None, rid if rid is not None else 0, track.filepath or "")
