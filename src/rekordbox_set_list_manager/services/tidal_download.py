"""Download Tidal tracks to tagged WAV files in the configured library folder."""

import logging
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

import platformdirs
import requests
import tidalapi
import tidalapi.exceptions
import tidalapi.media
from mutagen.wave import WAVE

from rekordbox_set_list_manager.services import _tidal_audio, _tidal_download_adapter
from rekordbox_set_list_manager.services._tidal_download_adapter import (
    PlaybackInfo,
    SegmentPlan,
    TidalCredentialsError,
)
from rekordbox_set_list_manager.utils import config

if TYPE_CHECKING:
    import threading
    from collections.abc import Callable

log = logging.getLogger(__name__)

__all__ = [
    "DownloadErrorKind",
    "DownloadResult",
    "PlaybackInfo",
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
        The templated ``.wav`` path the track was written to — the dedup key
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
    """

    filepath: Path
    track_id: str
    served_quality: str
    requested_quality: str
    downgraded: bool
    codec: str
    duration_s: float
    skipped_existing: bool = False


# Served-quality ordering mirrors tidalapi's Quality enum verbatim.
_QUALITY_RANK = {"LOW": 0, "HIGH": 1, "LOSSLESS": 2, "HI_RES_LOSSLESS": 3}
# Config floor names → minimum rank; unknown values coerce to the strictest
# floor (lossless) so a mistyped setting can never silently widen.
_FLOOR_MAP = {"any": 0, "low": 0, "high": 1, "lossless": 2}

_CFG_DOWNLOAD_DIR = "download_dir"
_CFG_TEMPLATE = "download_path_template"
_CFG_MIN_QUALITY = "tidal_download_min_quality"

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
    """Render *template* into a sanitized ``.wav`` path inside *root*.

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
        The resolved absolute ``.wav`` destination.

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
    final = path.with_name(f"{path.name}.wav")
    resolved = final.resolve()
    if not resolved.is_relative_to(Path(root).resolve()):
        raise TidalDownloadError(
            f"Download path escapes the download directory: {resolved}",
            kind=DownloadErrorKind.VALIDATION,
        )
    return resolved


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


def _validate_wav(part: Path, expected_s: int | None) -> None:
    """Verify the decoded ``.part`` file: RIFF/WAVE magic plus duration."""
    try:
        head = part.open("rb").read(12)
    except OSError as exc:
        raise TidalDownloadError(
            f"Decoded output could not be read: {exc}",
            kind=DownloadErrorKind.VALIDATION,
        ) from exc
    if not (head[:4] == b"RIFF" and head[8:12] == b"WAVE"):
        raise TidalDownloadError(
            "Decoded output is not a RIFF/WAVE file",
            kind=DownloadErrorKind.VALIDATION,
        )
    if expected_s:
        wave = WAVE(part)
        length = wave.info.length if wave.info is not None else 0.0
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
    elif isinstance(exc, PermissionError):
        kind, msg = DownloadErrorKind.AUTH, f"Tidal authorization failed: {exc}"
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
    """Download Tidal tracks as tagged, validated WAV files.

    The constructor applies the user-supplied client credentials to the
    session defensively so a directly-constructed service can never silently
    run under tidalapi's embedded (AAC-capped) client.

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
    ) -> None:
        try:
            _tidal_download_adapter.apply_client_credentials(session)
        except TidalCredentialsError as exc:
            raise TidalDownloadError(str(exc), kind=DownloadErrorKind.INVALID_CLIENT) from exc
        self._session = session
        raw_dir = download_dir if download_dir is not None else config.get(_CFG_DOWNLOAD_DIR)
        self._download_dir = (
            Path(str(raw_dir))
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

    def download_track(
        self,
        track_id: str,
        *,
        progress_cb: Callable[[float], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> DownloadResult:
        """Download *track_id* through the full pipeline to a tagged WAV.

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
        if final.exists():
            # The templated path is the dedup key — skip before any segment GET.
            return DownloadResult(
                filepath=final,
                track_id=track_id,
                served_quality=served,
                requested_quality=requested,
                downgraded=downgraded,
                codec=plan.codecs,
                duration_s=float(getattr(tidal_track, "duration", 0) or 0),
                skipped_existing=True,
            )
        part = final.with_name(f"{final.name}.part")
        final.parent.mkdir(parents=True, exist_ok=True)
        seg_paths: list[Path] = []
        try:
            seg_paths = self._fetch_segments(plan, part, progress_cb, cancel_event)
            payload = b"".join(seg.read_bytes() for seg in seg_paths)
            if plan.encrypted:
                raise TidalDownloadError(
                    "Encrypted streams are not yet supported by the decrypter",
                    kind=DownloadErrorKind.DECRYPT,
                )
            container = _check_payload_magic(payload)
            if container != plan.container:
                raise TidalDownloadError(
                    f"Payload container {container!r} does not match manifest"
                    f" container {plan.container!r}",
                    kind=DownloadErrorKind.VALIDATION,
                )
            _tidal_audio.decode_to_wav(
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
            _validate_wav(part, getattr(tidal_track, "duration", None))
            _tidal_audio.tag_wav(part, tidal_track, served, self._fetch_cover(tidal_track))
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
        """Fetch every URL in *plan* order into indexed ``.seg`` siblings."""
        seg_paths: list[Path] = []
        total = len(plan.urls)
        for index, url in enumerate(plan.urls):
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("download cancelled")
            data = _tidal_download_adapter.fetch_bytes(
                url, http=self._http, gate=self._gate, cancel=cancel_event
            )
            seg_path = part.with_suffix(f".seg{index}")
            seg_path.write_bytes(data)
            seg_paths.append(seg_path)
            if progress_cb is not None:
                progress_cb(_FETCH_SHARE * (index + 1) / total)
        return seg_paths

    def _fetch_cover(self, tidal_track: tidalapi.media.Track) -> bytes | None:
        """Best-effort cover art — a failure never fails the download."""
        try:
            album = getattr(tidal_track, "album", None)
            image = getattr(album, "image", None)
            if not callable(image):
                return None
            url = image(640)
            if not isinstance(url, str):
                return None
            return _tidal_download_adapter.fetch_cover(url, http=self._http, gate=self._gate)
        except Exception:
            log.debug("Cover art fetch failed", exc_info=True)
            return None
