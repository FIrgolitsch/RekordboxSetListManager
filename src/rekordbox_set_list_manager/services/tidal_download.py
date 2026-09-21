"""Download Tidal tracks to tagged AIFF files in the configured library folder."""

import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

import platformdirs
import requests
import tidalapi
import tidalapi.exceptions
import tidalapi.media
from mutagen.aiff import AIFF

from rekordbox_set_list_manager.services import (
    _tidal_audio,
    _tidal_crypto,
    _tidal_download_adapter,
)
from rekordbox_set_list_manager.services._tidal_download_adapter import (
    PlaybackInfo,
    RateGate,
    SegmentPlan,
    TidalAuthError,
    TidalCredentialsError,
)
from rekordbox_set_list_manager.services.skipped_item import SkippedItem
from rekordbox_set_list_manager.utils import config

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

log = logging.getLogger(__name__)

__all__ = [
    "DownloadBatch",
    "DownloadErrorKind",
    "DownloadResult",
    "PlaybackInfo",
    "RateGate",
    "SegmentPlan",
    "TidalDownloadError",
    "TidalDownloadService",
    "render_path",
]


class DownloadErrorKind(StrEnum):
    """Classified failure reason carried by :class:`TidalDownloadError`."""

    AUTH = "auth"
    INVALID_CLIENT = "invalid_client"
    RATE_LIMIT = "rate_limit"
    NETWORK = "network"
    DECRYPT = "decrypt"
    PREVIEW = "preview"
    QUALITY_CAP = "quality_cap"
    VALIDATION = "validation"
    DECODE = "decode"
    IO = "io"
    CANCELLED = "cancelled"


class TidalDownloadError(Exception):
    """Raised when a Tidal download fails.

    Attributes
    ----------
    kind : DownloadErrorKind
        The classified failure reason the UI layer switches on.
    """

    def __init__(self, msg: str, *, kind: DownloadErrorKind) -> None:
        super().__init__(msg)
        self.kind = kind


@dataclass(frozen=True)
class DownloadResult:
    """Outcome of a single ``download_track`` call.

    Attributes
    ----------
    filepath : Path
        The templated ``.aiff`` path the track was written to — the dedup key
        and the later re-match anchor.
    track_id : str
        The Tidal track id that was downloaded.
    served_quality : str
        The quality tier the server actually served (e.g. ``LOSSLESS``).
    requested_quality : str
        The quality tier requested (always ``HI_RES_LOSSLESS``).
    downgraded : bool
        ``True`` when the served tier is lossy (below ``LOSSLESS``).
    codec : str
        Manifest-reported codec string.
    duration_s : float
        Expected track duration in seconds.
    skipped_existing : bool
        ``True`` when the file already existed and nothing was fetched.
        Identity is verified before skipping: a file tagged
        ``TXXX:TIDAL_ID`` for a different track — or with a conflicting
        ``TSRC`` when the id frame is absent — does not satisfy the dedup;
        the download proceeds under a ``[track_id]``-suffixed name
        instead.  Files carrying neither tag keep the original path-only
        behavior.
    """

    filepath: Path
    track_id: str
    served_quality: str
    requested_quality: str
    downgraded: bool
    codec: str
    duration_s: float
    skipped_existing: bool = False


@dataclass(frozen=True)
class DownloadBatch:
    """Aggregate outcome of :meth:`TidalDownloadService.download_many`.

    Attributes
    ----------
    results : list[DownloadResult]
        One entry per delivered track, including ``skipped_existing`` ones.
    downgrades : list[DownloadResult]
        The subset of *results* flagged ``downgraded`` — delivered files
        that still need reporting, kept distinct from true failures.
    failures : list[SkippedItem]
        One entry per track that produced no file; ``reason`` carries the
        classified kind as ``"<kind>: <message>"``.
    cancelled : bool
        ``True`` when the batch stopped early via *cancel_event*; completed
        results and the interrupted track's failure are still reported.
    """

    results: list[DownloadResult]
    downgrades: list[DownloadResult]
    failures: list[SkippedItem]
    cancelled: bool = False


# Served-quality ordering mirrors tidalapi's Quality enum verbatim.
_QUALITY_RANK = {"LOW": 0, "HIGH": 1, "LOSSLESS": 2, "HI_RES_LOSSLESS": 3}
# Config floor names → minimum rank; unknown values coerce to the strictest
# floor (lossless) so a mistyped setting can never silently widen.
_FLOOR_MAP = {"any": 0, "low": 0, "high": 1, "lossless": 2}

_CFG_DOWNLOAD_DIR = "download_dir"
_CFG_TEMPLATE = "download_path_template"
_CFG_MIN_QUALITY = "tidal_download_min_quality"

# Bounded worker pool for segment fetches; the env var is an explicit
# operator override and wins over the constructor argument.
_DEFAULT_WORKERS = 4
_ENV_WORKERS = "SET_MANAGER_TIDAL_DOWNLOAD_WORKERS"

_DEFAULT_TEMPLATE = "{artist}/{album}/{tracknumber} - {title}"
_PLACEHOLDERS = {"artist", "title", "album", "tracknumber", "year", "isrc"}
_PLACEHOLDER_RE = re.compile(r"{([^{}]+)}")

_UNSAFE_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_COMPONENT_CAP = 100
_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {
    f"{prefix}{n}" for prefix in ("COM", "LPT") for n in range(1, 10)
}

# download_track reports progress in two phases: segment fetch covers
# [0, _FETCH_SHARE], decode covers [_FETCH_SHARE, 1].
_FETCH_SHARE = 0.6


class _MergedEvent(threading.Event):
    """Reads set when any source event is set — caller cancel or pool abort.

    ``fetch_bytes`` accepts a single cancel event; the worker pool feeds both
    the caller's event and its internal abort (a sibling segment failing)
    through this merged channel without mutating the caller's event.
    """

    def __init__(self, *sources: threading.Event) -> None:
        super().__init__()
        self._sources = sources

    def is_set(self) -> bool:
        """True when this event or any source event is set."""
        return super().is_set() or any(source.is_set() for source in self._sources)

    def wait(self, timeout: float | None = None) -> bool:
        """Block until any source event is set or *timeout* elapses.

        ``Event.wait`` reads only this instance's own flag — which the
        merged sources never set — so the base implementation would block
        forever; poll the merged state on a short interval instead.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self.is_set():
            if deadline is None:
                super().wait(0.05)
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return self.is_set()
            super().wait(min(remaining, 0.05))
        return True


def abort_aware_sleeper(abort: threading.Event) -> Callable[[float], None]:
    """Return a ``RateGate`` ``sleep`` callable wired to an abort event.

    A sleeper that merely early-returns on abort still lets the caller —
    ``acquire``/``backoff`` — stamp a release and let ``fetch_bytes`` fire
    one more GET; the sleeper must *raise* out of the gate lock instead.
    ``InterruptedError`` classifies to ``CANCELLED`` via
    ``_classify_download_error``, so a cancelled download shuts a 300 s
    ``Retry-After`` wait down promptly instead of sleeping it out.

    Parameters
    ----------
    abort : threading.Event
        Cooperative abort flag checked before and during the wait.

    Returns
    -------
    Callable[[float], None]
        A sleeper suitable for ``RateGate(sleep=...)``.

    Raises
    ------
    InterruptedError
        When *abort* is set before the wait starts or fires during it.
    """

    def _sleep(seconds: float) -> None:
        if abort.is_set() or abort.wait(seconds):
            raise InterruptedError("download aborted during rate-limit wait")

    return _sleep


def _sanitize_component(value: str) -> str:
    """One path component → filesystem-safe name (never empty)."""
    cleaned = _UNSAFE_NAME.sub("_", str(value))
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    # Drop dangling separators left behind by empty template fields.
    cleaned = cleaned.lstrip("-").strip(" .")
    cleaned = cleaned[:_COMPONENT_CAP].rstrip(" .")
    if cleaned.upper() in _WINDOWS_RESERVED:
        cleaned = f"_{cleaned}"
    return cleaned or "Unknown"


def _template_fields(tidal_track: tidalapi.media.Track) -> dict[str, str]:
    """Placeholder values sourced from the tidalapi track object."""
    artist_obj = getattr(tidal_track, "artist", None)
    album_obj = getattr(tidal_track, "album", None)
    track_num = getattr(tidal_track, "track_num", None)
    try:
        number = f"{int(track_num):02d}" if track_num else ""
    except TypeError, ValueError:
        number = str(track_num)
    release = getattr(album_obj, "release_date", None) or getattr(
        tidal_track, "stream_start_date", None
    )
    year = str(getattr(release, "year", "") or "")
    return {
        "artist": str(getattr(artist_obj, "name", "") or ""),
        "title": str(getattr(tidal_track, "title", "") or ""),
        "album": str(getattr(album_obj, "name", "") or ""),
        "tracknumber": number,
        "year": year,
        "isrc": str(getattr(tidal_track, "isrc", "") or ""),
    }


def render_path(tidal_track: tidalapi.media.Track, root: Path, template: str) -> Path:
    """Render *template* into a sanitized ``.aiff`` path inside *root*.

    Unknown ``{placeholder}`` names fall back to the default template; empty
    fields collapse stray separators; every component is sanitized and the
    resolved path must stay inside *root*.

    Parameters
    ----------
    tidal_track : tidalapi.media.Track
        The tidalapi track object the placeholders read from.
    root : Path
        The configured download root.
    template : str
        ``/``-separated placeholder template (subfolders allowed).

    Returns
    -------
    Path
        The resolved absolute ``.aiff`` destination.

    Raises
    ------
    TidalDownloadError
        ``VALIDATION`` when the resolved path escapes *root*.
    """
    used = template or _DEFAULT_TEMPLATE
    unknown = set(_PLACEHOLDER_RE.findall(used)) - _PLACEHOLDERS
    if unknown:
        log.warning(
            "Unknown download path-template placeholder(s) %s — using the default template",
            sorted(unknown),
        )
        used = _DEFAULT_TEMPLATE
    fields = _template_fields(tidal_track)
    # Sanitize field values before splitting so a "/" inside remote metadata
    # can never become a directory separator.
    fields = {
        key: re.sub(r"\s+", " ", _UNSAFE_NAME.sub("_", value)).strip()
        for key, value in fields.items()
    }
    rendered = _PLACEHOLDER_RE.sub(lambda match: fields.get(match.group(1), ""), used)
    components = [_sanitize_component(part) for part in rendered.split("/")]
    path = Path(root).joinpath(*components)
    final = path.with_name(f"{path.name}.aiff")
    resolved = final.resolve()
    if not resolved.is_relative_to(Path(root).resolve()):
        raise TidalDownloadError(
            f"Download path escapes the download directory: {resolved}",
            kind=DownloadErrorKind.VALIDATION,
        )
    return resolved


def _read_tag_identity(path: Path) -> tuple[str | None, str | None]:
    """Return ``(tidal_id, isrc)`` embedded in *path*; ``None``s when unreadable.

    ``TXXX:TIDAL_ID`` is written by :func:`tag_aiff`; ``TSRC`` carries the
    ISRC.  Both come back ``None`` for foreign files, untagged files, or
    anything mutagen cannot parse — the caller treats those as
    unidentifiable rather than as a mismatch.
    """
    try:
        audio = AIFF(path)
    except Exception:
        log.debug("Could not read tags from existing file %s", path, exc_info=True)
        return None, None
    tags = getattr(audio, "tags", None)
    if tags is None:
        return None, None
    tidal_id = None
    for frame in tags.getall("TXXX"):
        if getattr(frame, "desc", "") == "TIDAL_ID":
            text = frame.text
            tidal_id = str(text[0]) if text else None
    isrcs = tags.getall("TSRC")
    isrc = str(isrcs[0].text[0]) if isrcs and isrcs[0].text else None
    return tidal_id, isrc


def _is_same_track(path: Path, track_id: str, tidal_track: tidalapi.media.Track) -> bool:
    """Whether the existing file at *path* already holds *track_id*'s audio.

    A ``TXXX:TIDAL_ID`` frame decides outright.  Files predating it fall
    back to ``TSRC``: a matching ISRC is the same recording, while a
    *different* ISRC proves different audio.  Files with neither tag keep
    the original path-only dedup answer (``True``).
    """
    existing_id, existing_isrc = _read_tag_identity(path)
    if existing_id is not None:
        return existing_id == track_id
    if existing_isrc is not None:
        track_isrc = str(getattr(tidal_track, "isrc", "") or "")
        return not track_isrc or existing_isrc == track_isrc
    return True


def _check_payload_magic(payload: bytes) -> str:
    """Return the payload's container kind or reject it before decode."""
    if payload[:4] == b"fLaC":
        return "flac"
    if payload[4:8] == b"ftyp":
        return "mp4"
    raise TidalDownloadError(
        "Downloaded payload is neither FLAC nor MP4 (bad magic bytes)",
        kind=DownloadErrorKind.VALIDATION,
    )


def _validate_aiff(part: Path, expected_s: int | None) -> None:
    """Verify the decoded ``.part`` file: FORM/AIFF magic plus duration."""
    try:
        with part.open("rb") as fh:
            head = fh.read(12)
    except OSError as exc:
        raise TidalDownloadError(
            f"Decoded output could not be read: {exc}",
            kind=DownloadErrorKind.VALIDATION,
        ) from exc
    if not (head[:4] == b"FORM" and head[8:12] == b"AIFF"):
        raise TidalDownloadError(
            "Decoded output is not a FORM/AIFF file",
            kind=DownloadErrorKind.VALIDATION,
        )
    if expected_s:
        audio = AIFF(part)
        length = audio.info.length if audio.info is not None else 0.0
        if abs(length - expected_s) > max(2.0, expected_s * 0.05):
            raise TidalDownloadError(
                f"Decoded duration {length:.1f}s does not match track duration "
                f"{expected_s}s (likely a preview or truncated stream)",
                kind=DownloadErrorKind.VALIDATION,
            )


def _classify_download_error(exc: Exception) -> TidalDownloadError:
    """Map a builtin/adapter/tidalapi exception to a classified error kind.

    Ordering matters: ``TimeoutError``/``InterruptedError``/``PermissionError``
    and ``requests.RequestException`` are all ``OSError`` subclasses and must
    be tested before the generic ``OSError`` branch.
    """
    if isinstance(exc, TidalCredentialsError):
        kind, msg = DownloadErrorKind.INVALID_CLIENT, str(exc)
    elif isinstance(exc, (TimeoutError, tidalapi.exceptions.TooManyRequests)):
        kind, msg = DownloadErrorKind.RATE_LIMIT, f"Tidal rate limit exceeded: {exc}"
    elif isinstance(exc, InterruptedError):
        kind, msg = DownloadErrorKind.CANCELLED, "Download cancelled"
    elif isinstance(exc, TidalAuthError):
        kind, msg = DownloadErrorKind.AUTH, f"Tidal authorization failed: {exc}"
    elif isinstance(exc, PermissionError):
        # A bare PermissionError reaching here is a LOCAL filesystem denial
        # (unwritable download dir) — HTTP 401/403 arrives as the
        # TidalAuthError subclass handled above.
        kind, msg = DownloadErrorKind.IO, f"Could not write download output: {exc}"
    elif isinstance(exc, requests.RequestException):
        kind, msg = (
            DownloadErrorKind.NETWORK,
            f"Network error during Tidal download: {exc}",
        )
    elif isinstance(exc, RuntimeError):
        kind, msg = DownloadErrorKind.DECODE, str(exc)
    elif isinstance(exc, ValueError):
        kind, msg = DownloadErrorKind.VALIDATION, str(exc)
    elif isinstance(exc, tidalapi.exceptions.TidalAPIError):
        kind, msg = DownloadErrorKind.NETWORK, f"Tidal API error: {exc}"
    elif isinstance(exc, OSError):
        kind, msg = DownloadErrorKind.IO, f"Could not write download output: {exc}"
    else:
        kind, msg = DownloadErrorKind.NETWORK, f"Tidal download failed: {exc}"
    return TidalDownloadError(msg, kind=kind)


class TidalDownloadService:
    """Download Tidal tracks as tagged, validated AIFF files.

    The constructor re-applies the resolved client credentials to the
    session defensively — a user-supplied pair wins when configured,
    otherwise the session's embedded defaults stay in effect.

    Parameters
    ----------
    session : tidalapi.Session
        The authenticated tidalapi session.
    download_dir : Path | str | None
        Library root; ``None`` reads the ``download_dir`` config key, which
        itself defaults to ``user_music_path()/Set Manager Downloads``.
    template : str | None
        Path template; ``None`` reads ``download_path_template``.
    min_quality : str | None
        Minimum acceptable served quality; ``None`` reads
        ``tidal_download_min_quality`` (default ``lossless``).
    gate : RateGate | None
        Injectable rate gate (tests inject a fake clock).
    http : requests.Session | None
        Injectable HTTP session for segment/cover GETs.
    workers : int
        Segment-fetch pool size (default 4).  The
        ``SET_MANAGER_TIDAL_DOWNLOAD_WORKERS`` env var overrides it; an
        unparseable value is ignored with a warning.  Parallelism only
        overlaps request latency — every fetch still passes the one shared
        rate gate, so the global request rate never widens.
    """

    def __init__(
        self,
        session: tidalapi.Session,
        *,
        download_dir: Path | str | None = None,
        template: str | None = None,
        min_quality: str | None = None,
        gate: _tidal_download_adapter.RateGate | None = None,
        http: requests.Session | None = None,
        workers: int = _DEFAULT_WORKERS,
    ) -> None:
        _tidal_download_adapter.apply_client_credentials(session)
        self._session = session
        raw_dir = download_dir if download_dir is not None else config.get(_CFG_DOWNLOAD_DIR)
        self._download_dir = (
            # expanduser: a hand-edited config.json commonly writes "~" —
            # pathlib leaves it literal, which would create a "~" directory
            # under the CWD.
            Path(str(raw_dir)).expanduser()
            if raw_dir
            else Path(platformdirs.user_music_path()) / "Set Manager Downloads"
        )
        self._template = template or str(
            config.get(_CFG_TEMPLATE, _DEFAULT_TEMPLATE) or _DEFAULT_TEMPLATE
        )
        self._min_quality = str(
            min_quality or config.get(_CFG_MIN_QUALITY, "lossless") or "lossless"
        ).lower()
        self._gate = gate if gate is not None else _tidal_download_adapter.RateGate()
        self._http = http if http is not None else requests.Session()
        env_workers = os.environ.get(_ENV_WORKERS, "").strip()
        if env_workers:
            try:
                workers = int(env_workers)
            except ValueError:
                log.warning("Ignoring invalid %s=%r", _ENV_WORKERS, env_workers)
        self._workers = max(1, int(workers))

    def download_track(
        self,
        track_id: str,
        *,
        progress_cb: Callable[[float], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> DownloadResult:
        """Download *track_id* through the full pipeline to a tagged AIFF.

        Parameters
        ----------
        track_id : str
            The Tidal track id to download.
        progress_cb : Callable[[float], None] | None
            Optional callback receiving monotonically increasing progress
            fractions (segment fetch maps to [0, 0.6], decode to [0.6, 1]).
        cancel_event : threading.Event | None
            Optional cancel flag; setting it mid-download raises a CANCELLED
            error and leaves no partial files behind.

        Returns
        -------
        DownloadResult
            File path plus served/requested quality and downgrade flag.

        Raises
        ------
        TidalDownloadError
            Always carries a classified :class:`DownloadErrorKind`.
        """
        try:
            return self._download(str(track_id), progress_cb=progress_cb, cancel_event=cancel_event)
        except TidalDownloadError:
            raise
        except Exception as exc:
            raise _classify_download_error(exc) from exc

    def download_many(
        self,
        track_ids: Iterable[str],
        *,
        progress_cb: Callable[[float], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> DownloadBatch:
        """Download *track_ids* sequentially, isolating per-track failures.

        Tracks run one at a time — inter-track scheduling is the caller's
        concern; within a track the segment pool already overlaps fetches.
        A single track's failure is classified into ``failures`` and never
        aborts the batch.  *progress_cb* is forwarded verbatim to each
        ``download_track`` call — batch-level progress is
        ``(len(results) + len(failures)) / len(track_ids)``, which the
        caller can compute; no second callback shape is introduced.

        Parameters
        ----------
        track_ids : Iterable[str]
            Tidal track ids, processed in iteration order.
        progress_cb : Callable[[float], None] | None
            Per-track progress fractions, forwarded to ``download_track``.
        cancel_event : threading.Event | None
            Checked before every track.  When set mid-batch the in-flight
            track fails classified (``CANCELLED``) and the batch stops —
            delivered results and the failure are still reported.

        Returns
        -------
        DownloadBatch
            Delivered results, the downgraded subset, classified failures,
            and the cancelled flag.
        """
        results: list[DownloadResult] = []
        downgrades: list[DownloadResult] = []
        failures: list[SkippedItem] = []
        cancelled = False
        for track_id in (str(tid) for tid in track_ids):
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                break
            try:
                result = self.download_track(
                    track_id, progress_cb=progress_cb, cancel_event=cancel_event
                )
            except TidalDownloadError as exc:
                log.debug("Track %s failed classified (%s)", track_id, exc.kind.value)
                failures.append(SkippedItem(track_id, f"{exc.kind.value}: {exc}"))
                if exc.kind is DownloadErrorKind.CANCELLED:
                    cancelled = True
                    break
            # Broad catch is deliberate — one track must never sink the batch.
            except Exception as exc:
                log.debug("Unexpected download failure for track %s", track_id, exc_info=True)
                failures.append(SkippedItem(track_id, f"{DownloadErrorKind.NETWORK.value}: {exc}"))
            else:
                results.append(result)
                if result.downgraded:
                    downgrades.append(result)
        return DownloadBatch(
            results=results, downgrades=downgrades, failures=failures, cancelled=cancelled
        )

    def _download(
        self,
        track_id: str,
        *,
        progress_cb: Callable[[float], None] | None,
        cancel_event: threading.Event | None,
    ) -> DownloadResult:
        """Run the pipeline; builtin/adapter errors map in the caller."""
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("download cancelled")
        tidal_track = _tidal_download_adapter.fetch_track(self._session, track_id, gate=self._gate)
        info = _tidal_download_adapter.get_playback_info(self._session, track_id, gate=self._gate)
        if info.presentation == "PREVIEW":
            raise TidalDownloadError(
                f"Track {track_id} is only available as a preview"
                f" ({info.preview_reason or 'no reason given'})",
                kind=DownloadErrorKind.PREVIEW,
            )
        served = str(info.raw.get("audioQuality") or "")
        served_rank = _QUALITY_RANK.get(served.upper(), -1)
        floor = _FLOOR_MAP.get(self._min_quality, 2)
        if served_rank < floor:
            raise TidalDownloadError(
                f"Served quality {served or 'UNKNOWN'} is below the configured"
                f" minimum {self._min_quality!r} for track {track_id}",
                kind=DownloadErrorKind.QUALITY_CAP,
            )
        downgraded = served_rank < _QUALITY_RANK["LOSSLESS"]
        plan = _tidal_download_adapter.build_segment_plan(info.stream)
        final = render_path(tidal_track, self._download_dir, self._template)
        requested = str(tidalapi.media.Quality.hi_res_lossless)

        def _skipped(path: Path) -> DownloadResult:
            return DownloadResult(
                filepath=path,
                track_id=track_id,
                served_quality=served,
                requested_quality=requested,
                downgraded=downgraded,
                codec=plan.codecs,
                duration_s=float(getattr(tidal_track, "duration", 0) or 0),
                skipped_existing=True,
            )

        if final.exists():
            # The templated path is the dedup key — but only when the file
            # there actually holds this track's audio.  An embedded
            # TIDAL_ID/ISRC that disagrees means a different track rendered
            # to the same path; claiming it would silently pair the new id
            # with another track's audio.
            if _is_same_track(final, track_id, tidal_track):
                return _skipped(final)
            # Disambiguate by track id rather than overwrite — the earlier
            # download stays intact and both tracks keep working anchors.
            # A colliding suffixed name is re-verified before skipping.
            final = final.with_name(f"{final.stem} [{_sanitize_component(track_id)}].aiff")
            if final.exists() and _is_same_track(final, track_id, tidal_track):
                return _skipped(final)
        part = final.with_name(f"{final.name}.part")
        final.parent.mkdir(parents=True, exist_ok=True)
        seg_paths: list[Path] = []
        try:
            seg_paths = self._fetch_segments(plan, part, progress_cb, cancel_event)
            payload = b"".join(seg.read_bytes() for seg in seg_paths)
            if plan.encrypted:
                # Decrypt in memory — plaintext never touches a temp file; a
                # wrong key yields garbage that the magic check rejects below.
                try:
                    key, nonce = _tidal_crypto.decrypt_security_token(plan.key_id or "")
                    payload = _tidal_crypto.decrypt_file(payload, key, nonce)
                except _tidal_crypto.TidalCryptoError as exc:
                    raise TidalDownloadError(
                        f"Could not decrypt the stream for track {track_id}: {exc}",
                        kind=DownloadErrorKind.DECRYPT,
                    ) from exc
            container = _check_payload_magic(payload)
            if container != plan.container:
                raise TidalDownloadError(
                    f"Payload container {container!r} does not match manifest"
                    f" container {plan.container!r}",
                    kind=DownloadErrorKind.VALIDATION,
                )
            _tidal_audio.decode_to_aiff(
                payload,
                container,
                part,
                getattr(info.stream, "bit_depth", None),
                progress_cb=(
                    None
                    if progress_cb is None
                    else lambda frac: progress_cb(_FETCH_SHARE + (1 - _FETCH_SHARE) * frac)
                ),
                cancel_event=cancel_event,
            )
            _validate_aiff(part, getattr(tidal_track, "duration", None))
            _tidal_audio.tag_aiff(
                part, tidal_track, served, self._fetch_cover(tidal_track, cancel_event)
            )
            part.replace(final)
        finally:
            part.unlink(missing_ok=True)
            for seg in seg_paths:
                seg.unlink(missing_ok=True)
        return DownloadResult(
            filepath=final,
            track_id=track_id,
            served_quality=served,
            requested_quality=requested,
            downgraded=downgraded,
            codec=plan.codecs,
            duration_s=float(getattr(tidal_track, "duration", 0) or 0),
        )

    def _fetch_segments(
        self,
        plan: SegmentPlan,
        part: Path,
        progress_cb: Callable[[float], None] | None,
        cancel_event: threading.Event | None,
    ) -> list[Path]:
        """Fetch every URL into indexed ``.seg{i}`` siblings, bounded-parallel.

        A ``ThreadPoolExecutor`` of ``min(self._workers, len(urls))`` workers
        overlaps GET latency only — every fetch still passes the one shared
        ``RateGate``, so the global request rate never widens.  Each worker
        writes its own ``.seg{index}`` sibling and the caller concatenates by
        index position, never by completion order, so the merged payload is
        byte-exact regardless of which worker finishes first.

        On failure the pool's internal abort event is merged into the cancel
        channel: in-flight workers bail at their next cancel check, queued
        futures are cancelled outright, and every ``.seg`` part is removed.
        ``progress_cb`` fires from the caller's thread as futures complete —
        ``_FETCH_SHARE * done/total`` fractions stay monotonic.
        """
        urls = plan.urls
        total = len(urls)
        seg_paths = [part.with_suffix(f".seg{index}") for index in range(total)]
        if total == 0:
            return seg_paths
        abort = threading.Event()
        merged = _MergedEvent(*(e for e in (cancel_event, abort) if e is not None))

        def _fetch_one(index: int, url: str) -> None:
            data = _tidal_download_adapter.fetch_bytes(
                url, http=self._http, gate=self._gate, cancel=merged
            )
            if abort.is_set():
                # A sibling already failed — skip the write and surface the
                # abort so this worker's future completes promptly.
                raise InterruptedError("segment fetch aborted after a sibling failed")
            seg_paths[index].write_bytes(data)

        executor = ThreadPoolExecutor(
            max_workers=min(self._workers, total), thread_name_prefix="tidal-seg"
        )
        try:
            futures = [executor.submit(_fetch_one, index, url) for index, url in enumerate(urls)]
            for done, future in enumerate(as_completed(futures), start=1):
                # Abort is only ever set below this loop, so the first
                # exception surfaced here IS the root cause — never a
                # sibling's abort noise.
                future.result()
                if progress_cb is not None:
                    progress_cb(_FETCH_SHARE * done / total)
        except BaseException:
            abort.set()
            # Workers cannot be killed, but shutdown(wait=True) keeps the
            # .seg cleanup below from racing an in-flight write.
            executor.shutdown(wait=True, cancel_futures=True)
            for seg in seg_paths:
                seg.unlink(missing_ok=True)
            raise
        executor.shutdown(wait=True)
        return seg_paths

    def _fetch_cover(
        self, tidal_track: tidalapi.media.Track, cancel_event: threading.Event | None
    ) -> bytes | None:
        """Best-effort cover art — a failure never fails the download.

        A pending cancel is not a failure: ``InterruptedError`` propagates
        so the download aborts as CANCELLED instead of completing.
        """
        try:
            album = getattr(tidal_track, "album", None)
            image = getattr(album, "image", None)
            if not callable(image):
                return None
            url = image(640)
            if not isinstance(url, str):
                return None
            return _tidal_download_adapter.fetch_cover(
                url, http=self._http, gate=self._gate, cancel=cancel_event
            )
        except InterruptedError:
            raise
        except Exception:
            log.debug("Cover art fetch failed", exc_info=True)
            return None
