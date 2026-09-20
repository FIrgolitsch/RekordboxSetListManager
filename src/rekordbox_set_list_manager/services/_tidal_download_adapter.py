"""Adapter isolating the tidalapi/raw-HTTP surface used by track downloads.

All interaction with tidalapi's semi-private ``session.request.request`` API
(``tracks/{id}/playbackinfopostpaywall``), the ``session.config``
``client_id``/``client_secret``/``quality`` attributes, and raw segment/cover
GETs lives behind this seam so a tidalapi API change produces one localized
failure.

Error boundary: this module raises :class:`TidalCredentialsError` for missing
client credentials and lets tidalapi exceptions (``TooManyRequests``,
``ObjectNotFound``, ``StreamNotAvailable``) plus builtin ``PermissionError`` /
``TimeoutError`` / ``InterruptedError`` propagate; ``tidal_download`` maps
them onto its classified error kinds.  Keeping that edge one-directional
avoids a circular import (the service imports the adapter, never the
reverse).
"""

import hashlib
import logging
import os
import random
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import requests
import tidalapi
import tidalapi.media

from rekordbox_set_list_manager.utils import config

if TYPE_CHECKING:
    from collections.abc import Callable

log = logging.getLogger(__name__)

_ENV_ID = "SET_MANAGER_TIDAL_CLIENT_ID"
_ENV_SECRET = "SET_MANAGER_TIDAL_CLIENT_SECRET"  # noqa: S105 — env var NAME, not a secret
_CFG_ID = "tidal_client_id"
_CFG_SECRET = "tidal_client_secret"  # noqa: S105 — config key NAME, not a secret

_PLAYBACK_PARAMS = {"playbackmode": "STREAM", "assetpresentation": "FULL"}

_RATE_INTERVAL = 0.25  # seconds between Tidal-bound requests
_MAX_RETRIES = 5
_SEGMENT_HOST_SUFFIX = ".tidal.com"
_STATUS_TOO_MANY_REQUESTS = 429
_STATUS_SERVER_ERROR_MIN = 500


class TidalCredentialsError(Exception):
    """Raised when no user-supplied Tidal client credentials are configured."""


def resolve_client_credentials() -> tuple[str, str]:
    """Resolve the Tidal OAuth client pair, env var first then config.json.

    Empty or whitespace-only env values count as unset.  There is no built-in
    default — the embedded tidalapi credential is AAC-capped and is never
    used as a fallback.

    Returns
    -------
    tuple[str, str]
        ``(client_id, client_secret)`` plain strings.

    Raises
    ------
    TidalCredentialsError
        If either value is missing; the message names both env vars and both
        config keys.
    """
    client_id = os.environ.get(_ENV_ID, "").strip() or config.get(_CFG_ID)
    client_secret = os.environ.get(_ENV_SECRET, "").strip() or config.get(_CFG_SECRET)
    if not (client_id and client_secret):
        raise TidalCredentialsError(
            "Tidal client credentials not configured — set "
            f"{_ENV_ID}/{_ENV_SECRET} or {_CFG_ID}/{_CFG_SECRET} in config.json"
        )
    return str(client_id), str(client_secret)


def client_fingerprint(client_id: str) -> str:
    """Return a truncated sha256 of *client_id* for identity checks.

    This is an identity fingerprint, not a security boundary — it exists so a
    persisted session can be tied to the client that minted it without
    storing the credential itself.

    Parameters
    ----------
    client_id : str
        The resolved Tidal OAuth client id.

    Returns
    -------
    str
        First 16 hex chars of the SHA-256 digest.
    """
    return hashlib.sha256(client_id.encode()).hexdigest()[:16]


def apply_client_credentials(session: tidalapi.Session) -> str:
    """Apply the resolved client pair to *session* before any login/refresh.

    ``Config.__init__`` base64-decodes embedded defaults into plain
    attributes, so plain-string assignment is correct.

    Parameters
    ----------
    session : tidalapi.Session
        Session whose ``config.client_id``/``client_secret`` are set.

    Returns
    -------
    str
        The :func:`client_fingerprint` of the applied client id.
    """
    client_id, client_secret = resolve_client_credentials()
    session.config.client_id = client_id
    session.config.client_secret = client_secret
    return client_fingerprint(client_id)


@dataclass(frozen=True)
class PlaybackInfo:
    """Parsed stream plus the fields ``Stream.parse`` drops.

    Attributes
    ----------
    stream : tidalapi.media.Stream
        The parsed stream object (manifest, quality, bit depth).
    presentation : str
        Raw ``trackPresentation``/``assetPresentation`` value, ``"FULL"``
        when absent.
    preview_reason : str | None
        Raw ``previewReason`` when the track is a preview.
    raw : dict
        The full playback-info JSON response.
    """

    stream: tidalapi.media.Stream
    presentation: str
    preview_reason: str | None
    raw: dict


@dataclass(frozen=True)
class SegmentPlan:
    """Normalized fetch plan over both BTS-JSON and MPD/DASH manifests.

    Attributes
    ----------
    urls : tuple[str, ...]
        Segment URLs in media order — concatenation must follow this order.
    encrypted : bool
        Whether the payload is AES-encrypted (BTS ``keyId`` present).
    key_id : str | None
        Base64 security token for encrypted manifests.
    codecs : str
        Codec string reported by the manifest (e.g. ``FLAC``, ``MP4A``).
    container : str
        ``"flac"`` for raw FLAC payloads, ``"mp4"`` for MP4/M4A containers —
        the decode router keys on the container, never the codec name.
    mime_type : str
        Manifest-reported MIME type.
    """

    urls: tuple[str, ...]
    encrypted: bool
    key_id: str | None
    codecs: str
    container: str
    mime_type: str


class RateGate:
    """One gate for all Tidal traffic: playback-info, segment GETs, cover art.

    The clock, sleeper, and jitter source are injectable so tests never
    actually sleep.

    Parameters
    ----------
    interval : float
        Minimum spacing between released requests, in seconds.
    sleep : callable
        Sleep implementation (``time.sleep`` by default).
    monotonic : callable
        Monotonic clock (``time.monotonic`` by default).
    rng : callable
        Uniform random source used for backoff jitter.
    """

    def __init__(
        self,
        interval: float = _RATE_INTERVAL,
        *,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        rng: Callable[[], float] = random.random,
    ) -> None:
        self._interval = interval
        self._sleep = sleep
        self._monotonic = monotonic
        self._rng = rng
        self._last_release = 0.0
        # Guards the read-sleep-release critical section: without it two
        # workers can read a stale elapsed, both sleep, and stamp releases a
        # hair apart — silently doubling the global request rate.  The lock
        # *is* the spacing mechanism under a worker pool.
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """Sleep until *interval* has elapsed since the last release.

        The whole check-sleep-release sequence runs under the gate lock, so
        concurrent callers simply serialize behind whichever worker holds it
        and releases stay globally spaced.
        """
        with self._lock:
            elapsed = self._monotonic() - self._last_release
            wait = self._interval - elapsed
            if wait > 0:
                self._sleep(wait)
            self._last_release = self._monotonic()

    def backoff(self, attempt: int, hint: float = 0.0) -> float:
        """Sleep a jittered exponential backoff (or *hint*, when larger).

        The sleep runs under the gate lock — a 429/5xx backoff pauses
        releases globally for every worker, which is the conservative and
        intended behavior.

        Parameters
        ----------
        attempt : int
            Zero-based retry attempt number.
        hint : float
            Server-supplied delay (``Retry-After``); used when it exceeds the
            computed backoff.

        Returns
        -------
        float
            The delay actually slept.
        """
        delay = min(60.0, (2**attempt) * self._interval) * (1.0 + self._rng() * 0.5)
        delay = max(delay, hint)
        with self._lock:
            self._sleep(delay)
            self._last_release = self._monotonic()
        return delay


def _check_segment_url(url: str) -> None:
    """Reject non-HTTPS or non-Tidal hosts — manifests are untrusted data."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.scheme != "https" or not host.endswith(_SEGMENT_HOST_SUFFIX):
        raise ValueError(f"Refusing segment URL outside https://*{_SEGMENT_HOST_SUFFIX}: {url}")


def _retry_after(resp: requests.Response) -> float:
    """Parse a ``Retry-After`` header, tolerating non-numeric values."""
    try:
        return float(resp.headers.get("Retry-After", 0) or 0)
    except TypeError, ValueError:
        return 0.0


def fetch_bytes(
    url: str,
    *,
    http: requests.Session,
    gate: RateGate,
    cancel: threading.Event | None = None,
) -> bytes:
    """GET *url* through the rate gate with status-first retry handling.

    Status is always checked before the body is parsed — 429 bodies are not
    valid JSON.  Transport-level failures (connection refused/reset, connect
    or read timeouts, chunked mid-body aborts) retry under the same backoff
    and re-raise the final :class:`requests.RequestException` so the caller
    can classify them as NETWORK (never RATE_LIMIT).

    Parameters
    ----------
    url : str
        Segment or cover URL; must be ``https`` on a ``*.tidal.com`` host.
    http : requests.Session
        HTTP session used for the GET (injectable for tests).
    gate : RateGate
        Rate gate covering every Tidal-bound request.
    cancel : threading.Event | None
        Optional cancel flag checked before each attempt and per received
        chunk.

    Returns
    -------
    bytes
        The response body.

    Raises
    ------
    ValueError
        If *url* fails the scheme/host allowlist before any request.
    PermissionError
        On 401/403 (caller maps to AUTH).
    TimeoutError
        When the retry ceiling is exhausted on 429/5xx statuses (caller maps
        to RATE_LIMIT).
    InterruptedError
        When *cancel* is set — checked before every attempt and per
        received chunk (caller maps to CANCELLED).
    requests.RequestException
        On persistent transport failure or a final non-retried error status.
    """
    _check_segment_url(url)
    for attempt in range(_MAX_RETRIES):
        if cancel is not None and cancel.is_set():
            raise InterruptedError("download cancelled")
        gate.acquire()
        try:
            resp = http.get(url, stream=True, timeout=(10, 30))
            # Status code first — a 429 body is not valid JSON.
            if resp.status_code == _STATUS_TOO_MANY_REQUESTS:
                if attempt == _MAX_RETRIES - 1:
                    break
                gate.backoff(attempt, hint=_retry_after(resp))
                continue
            if resp.status_code in (401, 403):
                raise PermissionError(f"Tidal segment GET refused ({resp.status_code}): {url}")
            if resp.status_code >= _STATUS_SERVER_ERROR_MIN:
                if attempt == _MAX_RETRIES - 1:
                    break
                gate.backoff(attempt)
                continue
            resp.raise_for_status()
            chunks = []
            for chunk in resp.iter_content(1 << 16):
                if cancel is not None and cancel.is_set():
                    raise InterruptedError("download cancelled mid-body")
                chunks.append(chunk)
            return b"".join(chunks)
        except requests.RequestException:
            if attempt == _MAX_RETRIES - 1:
                raise
            gate.backoff(attempt)
    raise TimeoutError(f"Tidal request still failing after {_MAX_RETRIES} attempts: {url}")


def fetch_track(session: tidalapi.Session, track_id: str, *, gate: RateGate):
    """Fetch the tidalapi Track for *track_id* through the gate.

    When the returned track carries no album payload, the call is retried
    once with ``with_album=True`` so tagging has album metadata.

    Parameters
    ----------
    session : tidalapi.Session
        Authenticated session.
    track_id : str
        Tidal track id.
    gate : RateGate
        Rate gate covering the request.

    Returns
    -------
    tidalapi.media.Track
        The track object (album populated when available).
    """
    gate.acquire()
    tidal_track = session.track(track_id)
    album_missing = getattr(tidal_track, "album", None) is None or (
        getattr(tidal_track.album, "id", None) is None
    )
    if album_missing:
        gate.acquire()
        tidal_track = session.track(track_id, with_album=True)
    return tidal_track


def get_playback_info(session: tidalapi.Session, track_id: str, *, gate: RateGate) -> PlaybackInfo:
    """Fetch raw playback info for *track_id*.

    ``Stream.parse`` drops ``trackPresentation``/``previewReason``, so the raw
    response JSON is kept alongside the parsed stream — the same dict feeds
    the preview check and the served-quality floor check.

    Parameters
    ----------
    session : tidalapi.Session
        Authenticated session; ``config.quality`` is forced to
        ``hi_res_lossless`` before the call so tidalapi's ``low_320k``
        default can never silently serve AAC.
    track_id : str
        Tidal track id.
    gate : RateGate
        Rate gate covering the request.

    Returns
    -------
    PlaybackInfo
        Parsed stream plus presentation/preview fields and the raw JSON.
    """
    session.config.quality = tidalapi.media.Quality.hi_res_lossless
    gate.acquire()
    resp = session.request.request(
        "GET",
        f"tracks/{track_id}/playbackinfopostpaywall",
        params={**_PLAYBACK_PARAMS, "audioquality": session.config.quality},
    )
    raw = resp.json()
    stream = tidalapi.media.Stream().parse(raw)
    presentation = raw.get("trackPresentation") or raw.get("assetPresentation") or "FULL"
    return PlaybackInfo(
        stream=stream,
        presentation=presentation,
        preview_reason=raw.get("previewReason"),
        raw=raw,
    )


def build_segment_plan(stream: tidalapi.media.Stream) -> SegmentPlan:
    """Normalize a parsed :class:`tidalapi.media.Stream` into a fetch plan.

    Both BTS-JSON and MPD/DASH manifests normalize to the same shape; MPD
    URLs arrive pre-expanded in media order by tidalapi's ``DashInfo``.

    Parameters
    ----------
    stream : tidalapi.media.Stream
        Parsed stream object (from :func:`get_playback_info`).

    Returns
    -------
    SegmentPlan
        Ordered URLs, encryption flag/key, and the container kind used to
        route the decode backend.
    """
    manifest = stream.get_stream_manifest()
    urls = list(manifest.urls)
    # MPD/DASH keeps the initialization segment (ftyp+moov header) separate on
    # dash_info.first_url — it must precede the media segments or the
    # concatenated payload has no MP4 header and cannot decode.
    first_url = str(getattr(getattr(manifest, "dash_info", None), "first_url", "") or "")
    if first_url and first_url not in urls:
        urls.insert(0, first_url)
    ext = str(manifest.file_extension or "").lower()
    container = "flac" if "flac" in ext else "mp4"
    return SegmentPlan(
        urls=tuple(urls),
        encrypted=bool(manifest.is_encrypted),
        key_id=manifest.encryption_key,
        codecs=str(manifest.codecs),
        container=container,
        mime_type=str(manifest.mime_type),
    )


def fetch_cover(url: str | None, *, http: requests.Session, gate: RateGate) -> bytes | None:
    """Best-effort cover-art GET — a failure never fails the download.

    Parameters
    ----------
    url : str | None
        Cover image URL; ``None`` or a disallowed host simply yields
        ``None``.
    http : requests.Session
        HTTP session used for the GET.
    gate : RateGate
        Rate gate covering the request.

    Returns
    -------
    bytes | None
        The image bytes, or ``None`` on any failure.
    """
    if not url:
        return None
    try:
        return fetch_bytes(url, http=http, gate=gate)
    except Exception:
        log.debug("Cover art fetch failed for %s", url, exc_info=True)
        return None


__all__ = [
    "PlaybackInfo",
    "RateGate",
    "SegmentPlan",
    "TidalCredentialsError",
    "apply_client_credentials",
    "build_segment_plan",
    "client_fingerprint",
    "fetch_bytes",
    "fetch_cover",
    "fetch_track",
    "get_playback_info",
    "resolve_client_credentials",
]
