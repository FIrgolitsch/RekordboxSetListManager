"""Decode Tidal payloads to AIFF and tag them with mutagen.

AIFF is the output container: uncompressed PCM like WAV, but Rekordbox
reads full ID3v2.4 metadata from it — including APIC artwork, which WAV
can never surface (Rekordbox only reads RIFF INFO text from WAV).
soundfile (bundled libsndfile) is the primary decode path for FLAC
payloads; MP4/M4A containers route to an ``ffmpeg`` subprocess because
libsndfile has no MP4/AAC support.  Both functions raise plain builtin
errors — ``RuntimeError`` for decode failures, ``InterruptedError`` for
cancellation — and ``tidal_download`` maps them onto its classified error
kinds; the import edge stays one-directional (service → audio) to avoid a
cycle.
"""

import io
import logging
import shutil
import subprocess
from typing import TYPE_CHECKING

import soundfile as sf
from mutagen.aiff import AIFF
from mutagen.id3 import APIC, TALB, TBPM, TDRC, TIT2, TPE1, TRCK, TSRC, TXXX
from PIL import Image

if TYPE_CHECKING:
    import threading
    from collections.abc import Callable
    from pathlib import Path

    import tidalapi.media

log = logging.getLogger(__name__)

# Source bit depth above which the output is written as 24-bit PCM.
_BIT_DEPTH_24_THRESHOLD = 16


def _raise_if_cancelled(cancel_event: threading.Event | None) -> None:
    """Raise ``InterruptedError`` when *cancel_event* is set."""
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("decode cancelled")


def decode_to_aiff(
    payload: bytes,
    container: str,
    dest_part: Path,
    bit_depth: int | None,
    *,
    progress_cb: Callable[[float], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> None:
    """Decode *payload* to an AIFF file at *dest_part*.

    Parameters
    ----------
    payload : bytes
        The (already decrypted) audio payload.
    container : str
        ``"flac"`` routes to soundfile; ``"mp4"`` routes to the ffmpeg
        subprocess fallback.
    dest_part : Path
        Destination ``.part`` path — written in place, never renamed here.
    bit_depth : int | None
        Source bit depth; ``None`` (AAC serves report none) falls back to 16.
        Values above 16 produce a 24-bit PCM AIFF.
    progress_cb : Callable[[float], None] | None
        Optional callback receiving the decoded fraction (0..1) per block.
    cancel_event : threading.Event | None
        Optional cancel flag checked per block (soundfile path only — the
        ffmpeg subprocess runs to completion or fails).

    Raises
    ------
    InterruptedError
        When *cancel_event* is set mid-decode.
    RuntimeError
        When no decode backend can handle the payload.
    """
    bit_depth = bit_depth or 16
    if container == "flac":
        _decode_via_soundfile(payload, dest_part, bit_depth, progress_cb, cancel_event)
        return
    _decode_via_ffmpeg(payload, dest_part, bit_depth)


def _decode_via_soundfile(
    payload: bytes,
    dest_part: Path,
    bit_depth: int,
    progress_cb: Callable[[float], None] | None,
    cancel_event: threading.Event | None,
) -> None:
    """Stream-decode a FLAC payload to PCM AIFF via bundled libsndfile."""
    buf = io.BytesIO(payload)
    # libsndfile cannot sniff the codec on a bare BytesIO — the .name hint
    # supplies the container format (the explicit format= kwarg is rejected
    # for existing files).
    buf.name = "payload.flac"  # type: ignore[attr-defined]
    subtype = "PCM_24" if bit_depth > _BIT_DEPTH_24_THRESHOLD else "PCM_16"
    try:
        with (
            sf.SoundFile(buf, "r") as fin,
            sf.SoundFile(
                str(dest_part),
                "w",
                samplerate=fin.samplerate,
                channels=fin.channels,
                format="AIFF",
                subtype=subtype,
            ) as fout,
        ):
            # libsndfile reports frames == -1 for unknown-length streams;
            # -1 is truthy, so clamp to a positive denominator — otherwise
            # progress fractions go negative and the block size degenerates
            # to single frames.
            total = fin.frames if fin.frames and fin.frames > 0 else 1
            blocksize = max(1, total // 100) if total > 1 else 65536
            written = 0
            for block in fin.blocks(blocksize, dtype="int32", always_2d=True):
                _raise_if_cancelled(cancel_event)
                fout.write(block)
                written += len(block)
                if progress_cb is not None:
                    progress_cb(min(1.0, written / total))
    except InterruptedError:
        raise
    except Exception as exc:
        raise RuntimeError(f"soundfile could not decode the payload: {exc}") from exc


def _decode_via_ffmpeg(payload: bytes, dest_part: Path, bit_depth: int) -> None:
    """Decode an MP4/M4A payload to AIFF via an ``ffmpeg`` subprocess.

    ffmpeg needs a seekable input path, so the payload is staged to a sibling
    temp file inside the ``.part`` workspace and always removed afterwards.
    """
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError(
            "ffmpeg is required to decode this stream format — install ffmpeg or restore soundfile"
        )
    src_tmp = dest_part.with_name(f"{dest_part.name}.src")
    try:
        src_tmp.write_bytes(payload)
        subprocess.run(  # noqa: S603 — fixed arg list, absolute binary, no shell
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(src_tmp),
                "-map",
                "a:0",
                "-vn",
                "-sn",
                "-dn",
                "-c:a",
                # AIFF stores PCM big-endian, unlike WAV's little-endian.
                "pcm_s24be" if bit_depth > _BIT_DEPTH_24_THRESHOLD else "pcm_s16be",
                # dest_part is a `.aiff.part` staging name — ffmpeg picks the
                # output muxer from the extension and `.part` matches nothing,
                # so the format must be declared explicitly.
                "-f",
                "aiff",
                str(dest_part),
            ],
            check=True,
            timeout=300,
        )
    except (subprocess.SubprocessError, FileNotFoundError) as exc:
        # SubprocessError covers CalledProcessError and TimeoutExpired; a
        # FileNotFoundError means the binary vanished between which() and
        # run().  All three are decode failures — surface them as
        # RuntimeError so the caller classifies DECODE, not NETWORK/IO.
        raise RuntimeError(f"ffmpeg could not decode the payload: {exc}") from exc
    finally:
        src_tmp.unlink(missing_ok=True)


def tag_aiff(
    path: Path,
    tidal_track: tidalapi.media.Track,
    served_quality: str,
    cover: bytes | None,
) -> None:
    """Tag the AIFF at *path* with ID3v2.4, including APIC artwork.

    Rekordbox reads AIFF metadata from the ID3 chunk directly, so no
    dual-write is needed.  Fields are guarded individually — tidalapi
    attributes can be ``None`` depending on how the track was fetched.  A
    ``TXXX:TIDAL_QUALITY`` frame persists the served quality tier for a
    future re-download pass.

    Parameters
    ----------
    path : Path
        The AIFF file to tag in place.
    tidal_track : tidalapi.media.Track
        The tidalapi track object (not the app model — it carries album and
        track-number fields the model lacks).
    served_quality : str
        The quality tier the server actually served (e.g. ``LOSSLESS``).
    cover : bytes | None
        JPEG cover bytes for the APIC frame, when a fetch succeeded.
    """
    audio = AIFF(path)
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags
    if tags is None:  # pragma: no cover — add_tags() always populates it
        raise RuntimeError(f"Could not create an ID3 tag chunk in {path}")
    tags.add(TIT2(encoding=3, text=str(tidal_track.title or "")))
    artist = getattr(tidal_track, "artist", None)
    tags.add(TPE1(encoding=3, text=str(getattr(artist, "name", "") or "")))
    album = getattr(tidal_track, "album", None)
    if album is not None and getattr(album, "name", None):
        tags.add(TALB(encoding=3, text=str(album.name)))
    track_num = getattr(tidal_track, "track_num", None)
    if track_num:
        tags.add(TRCK(encoding=3, text=str(track_num)))
    if getattr(tidal_track, "isrc", None):
        tags.add(TSRC(encoding=3, text=str(tidal_track.isrc)))
    bpm = getattr(tidal_track, "bpm", None)
    if bpm:
        try:
            tags.add(TBPM(encoding=3, text=str(int(bpm))))
        except TypeError, ValueError, OverflowError:
            # A cosmetic tag must never sink a completed download — a
            # non-numeric or extreme bpm value is simply omitted.
            log.debug("Skipping TBPM tag for non-numeric bpm %r", bpm)
    release_date = getattr(album, "release_date", None) if album is not None else None
    if release_date:
        tags.add(TDRC(encoding=3, text=str(release_date)[:10]))
    tags.add(TXXX(encoding=3, desc="TIDAL_QUALITY", text=served_quality))
    if cover:
        data, mime = _normalize_cover(cover)
        tags.add(APIC(encoding=3, mime=mime, type=3, data=data))
    tags.save(path)


def _normalize_cover(cover: bytes) -> tuple[bytes, str]:
    """Return ``(data, mime)`` for embedding, re-encoding progressive JPEGs.

    Rekordbox cannot decode progressive JPEG — Tidal serves covers in that
    encoding, so they are re-saved as baseline.  Any decode or encode
    failure keeps the original bytes: a cosmetic tag must never sink a
    completed download.
    """
    if cover.startswith(b"\x89PNG"):
        return cover, "image/png"
    if cover.startswith(b"\xff\xd8"):
        try:
            img = Image.open(io.BytesIO(cover))
            if img.info.get("progressive") or img.info.get("progression"):
                out = io.BytesIO()
                img.convert("RGB").save(out, "JPEG", quality=95)
                return out.getvalue(), "image/jpeg"
        except Exception:  # noqa: BLE001 — best-effort cover normalization
            log.debug("Could not inspect/re-encode cover art; embedding as-is")
        return cover, "image/jpeg"
    return cover, "image/jpeg"
