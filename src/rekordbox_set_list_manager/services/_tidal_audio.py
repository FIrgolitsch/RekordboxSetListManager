"""Decode Tidal payloads to WAV and tag them with mutagen.

soundfile (bundled libsndfile) is the primary decode path for FLAC payloads;
MP4/M4A containers route to an ``ffmpeg`` subprocess because libsndfile has
no MP4/AAC support.  Both functions raise plain builtin errors —
``RuntimeError`` for decode failures, ``InterruptedError`` for cancellation —
and ``tidal_download`` maps them onto its classified error kinds; the import
edge stays one-directional (service → audio) to avoid a cycle.
"""

import io
import logging
import shutil
import struct
import subprocess
from typing import TYPE_CHECKING

import soundfile as sf
from mutagen.id3 import APIC, TALB, TBPM, TDRC, TIT2, TPE1, TRCK, TSRC, TXXX
from mutagen.wave import WAVE
from PIL import Image

if TYPE_CHECKING:
    import threading
    from collections.abc import Callable
    from pathlib import Path

    import tidalapi.media

log = logging.getLogger(__name__)

# Source bit depth above which the WAV is written as 24-bit PCM.
_BIT_DEPTH_24_THRESHOLD = 16

# RIFF INFO fields written in this order — Rekordbox reads WAV metadata from
# this list rather than the ID3 chunk, so both must be present.
_INFO_FIELD_ORDER = ("INAM", "IART", "IPRD", "ITRK", "IGNR", "ISRC", "ICRD")

# "RIFF" + size + "WAVE" — the fixed 12-byte RIFF/WAVE preamble.
_RIFF_HEADER_SIZE = 12


def _raise_if_cancelled(cancel_event: threading.Event | None) -> None:
    """Raise ``InterruptedError`` when *cancel_event* is set."""
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("decode cancelled")


def decode_to_wav(
    payload: bytes,
    container: str,
    dest_part: Path,
    bit_depth: int | None,
    *,
    progress_cb: Callable[[float], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> None:
    """Decode *payload* to a WAV file at *dest_part*.

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
        Values above 16 produce a 24-bit PCM WAV.
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
    """Stream-decode a FLAC payload to PCM WAV via bundled libsndfile."""
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
                format="WAV",
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
    """Decode an MP4/M4A payload to WAV via an ``ffmpeg`` subprocess.

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
                "pcm_s24le" if bit_depth > _BIT_DEPTH_24_THRESHOLD else "pcm_s16le",
                # dest_part is a `.wav.part` staging name — ffmpeg picks the
                # output muxer from the extension and `.part` matches nothing,
                # so the format must be declared explicitly.
                "-f",
                "wav",
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


def tag_wav(
    path: Path,
    tidal_track: tidalapi.media.Track,
    served_quality: str,
    cover: bytes | None,
) -> None:
    """Tag the WAV at *path* with both ID3v2 and RIFF ``LIST``/``INFO``.

    The ID3v2 tag lands in an ``ID3 `` RIFF chunk — mutagen writes ``id3 ``
    lowercase, which is renamed because Rekordbox only honours artwork
    (APIC) from the uppercase form it writes itself; mutagen still reads
    both casings back.  The same text fields are dual-written as a
    ``LIST``/``INFO`` chunk because Rekordbox reads WAV text metadata from
    the INFO list.  Fields are guarded individually — tidalapi attributes
    can be ``None`` depending on how the track was fetched.  A
    ``TXXX:TIDAL_QUALITY`` frame persists the served quality tier for a
    future re-download pass.

    Parameters
    ----------
    path : Path
        The WAV file to tag in place.
    tidal_track : tidalapi.media.Track
        The tidalapi track object (not the app model — it carries album and
        track-number fields the model lacks).
    served_quality : str
        The quality tier the server actually served (e.g. ``LOSSLESS``).
    cover : bytes | None
        JPEG cover bytes for the APIC frame, when a fetch succeeded.
    """
    audio = WAVE(path)
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
    _uppercase_id3_chunk(path)
    _write_riff_info(path, _info_fields(tidal_track))


def _info_fields(tidal_track: tidalapi.media.Track) -> list[tuple[str, str]]:
    """Collect the RIFF INFO fields present on *tidal_track*.

    Every field is guarded like its ID3 counterpart — absent source data is
    omitted entirely, never serialized as the literal string ``None``.
    ``IGNR`` has no source field; the genre default ``n/a`` is written
    explicitly.
    """
    artist = getattr(tidal_track, "artist", None)
    album = getattr(tidal_track, "album", None)
    values: dict[str, str] = {"IGNR": "n/a"}
    title = getattr(tidal_track, "title", None)
    if title:
        values["INAM"] = str(title)
    artist_name = getattr(artist, "name", None)
    if artist_name:
        values["IART"] = str(artist_name)
    album_name = getattr(album, "name", None) if album is not None else None
    if album_name:
        values["IPRD"] = str(album_name)
    track_num = getattr(tidal_track, "track_num", None)
    if track_num:
        try:
            values["ITRK"] = f"{int(track_num):02d}"
        except TypeError, ValueError:
            values["ITRK"] = str(track_num)
    isrc = getattr(tidal_track, "isrc", None)
    if isrc:
        values["ISRC"] = str(isrc)
    release_date = getattr(album, "release_date", None) if album is not None else None
    if release_date:
        values["ICRD"] = str(release_date)[:10]
    return [(tag_id, values[tag_id]) for tag_id in _INFO_FIELD_ORDER if tag_id in values]


def _build_info_chunk(fields: list[tuple[str, str]]) -> bytes:
    """Serialize a ``LIST``/``INFO`` chunk.

    Layout: ``LIST`` + u32le size + ``INFO`` + per-field subchunks of
    ``<4cc>`` + u32le size + utf-8 value + trailing NUL; every subchunk and
    the LIST chunk itself pad to an even byte boundary per the RIFF spec.
    """
    subchunks = bytearray()
    for tag_id, value in fields:
        data = value.encode("utf-8") + b"\x00"
        subchunks += tag_id.encode("ascii") + struct.pack("<I", len(data)) + data
        if len(data) % 2:
            subchunks += b"\x00"
    payload = b"INFO" + bytes(subchunks)
    chunk = b"LIST" + struct.pack("<I", len(payload)) + payload
    if len(payload) % 2:
        chunk += b"\x00"
    return chunk


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


def _uppercase_id3_chunk(path: Path) -> None:
    """Rename the ``id3 `` chunk to ``ID3 `` — the casing Rekordbox reads.

    Rekordbox reads WAV artwork only from an uppercase ``ID3 `` chunk (the
    form it writes itself); mutagen's lowercase ``id3 `` is ignored.  The
    chunk id is patched in place — a four-byte write that leaves every
    chunk size and the RIFF length untouched.
    """
    with path.open("r+b") as fh:
        file_size = fh.seek(0, 2)
        offset = _RIFF_HEADER_SIZE
        while offset + 8 <= file_size:
            fh.seek(offset)
            cid, csize = struct.unpack("<4sI", fh.read(8))
            if cid == b"id3 ":
                fh.seek(offset)
                fh.write(b"ID3 ")
                return
            offset += 8 + csize + (csize % 2)


def _write_riff_info(path: Path, fields: list[tuple[str, str]]) -> None:
    """Insert the ``LIST``/``INFO`` chunk into the WAV at *path*.

    Runs on the ``.part`` file before its atomic rename — mutagen only
    manages the ``id3 `` chunk, so the RIFF INFO list is appended manually.
    An existing INFO list (re-tagging a tagged WAV) is replaced; the RIFF
    size field is fixed up either way.
    """
    if not fields:
        return
    chunk = _build_info_chunk(fields)
    with path.open("r+b") as fh:
        head = fh.read(_RIFF_HEADER_SIZE)
        if len(head) < _RIFF_HEADER_SIZE or head[:4] != b"RIFF" or head[8:] != b"WAVE":
            raise RuntimeError(f"Cannot write RIFF INFO into a non-WAVE file: {path}")
        fh.seek(0, 2)
        file_size = fh.tell()
        # Walk top-level chunk headers; remember byte spans of LIST/INFO
        # chunks so a stale list can be replaced instead of duplicated.
        spans: list[tuple[int, int]] = []
        offset = _RIFF_HEADER_SIZE
        while offset + 8 <= file_size:
            fh.seek(offset)
            cid, csize = struct.unpack("<4sI", fh.read(8))
            padded = csize + (csize % 2)
            if cid == b"LIST":
                fh.seek(offset + 8)
                if fh.read(4) == b"INFO":
                    spans.append((offset, 8 + padded))
            offset += 8 + padded
        if spans:
            fh.seek(0)
            data = bytearray(fh.read())
            for start, length in reversed(spans):
                del data[start : start + length]
            data += chunk
            struct.pack_into("<I", data, 4, len(data) - 8)
            fh.seek(0)
            fh.write(data)
            fh.truncate()
        else:
            fh.seek(0, 2)
            fh.write(chunk)
            fh.seek(4)
            fh.write(struct.pack("<I", file_size + len(chunk) - 8))
