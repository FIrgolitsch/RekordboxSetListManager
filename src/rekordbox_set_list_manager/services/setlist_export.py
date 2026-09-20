"""Export set lists to timestamped TXT, CSV, and M3U8 files."""

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from rekordbox_set_list_manager.services.skipped_item import SkippedItem

if TYPE_CHECKING:
    from collections.abc import Collection
    from pathlib import Path

    from rekordbox_set_list_manager.models.project import Project
    from rekordbox_set_list_manager.models.track import Track


class SetlistExportError(Exception):
    """Raised when a setlist export artifact cannot be written.

    Attributes
    ----------
    path : Path | None
        The artifact path whose write failed; ``None`` until a write fails.

    """

    path: Path | None = None


@dataclass(frozen=True)
class SetlistExportResult:
    """Files written plus per-track skips across all requested formats.

    Attributes
    ----------
    written : list[Path]
        Artifact paths written, in the order they were produced.
    skipped : list[SkippedItem]
        Tracks skipped by a renderer, each with the reason why.

    """

    written: list[Path] = field(default_factory=list)
    skipped: list[SkippedItem] = field(default_factory=list)


_UNSAFE_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_NAME_CAP = 60


def _safe_name(name: str) -> str:
    """Project name → filename-safe basename component (never empty)."""
    cleaned = _UNSAFE_NAME.sub("_", name).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:_NAME_CAP] or "setlist"


def _one_line(value: str) -> str:
    """Collapse CR/LF runs to a space so an emitted line stays single-line."""
    return re.sub(r"[\r\n]+", " ", value)


def export_setlist(
    project: Project,
    out_dir: Path,
    formats: Collection[str],
    *,
    include_streaming_uris: bool = True,
    now: datetime | None = None,
) -> SetlistExportResult:
    """Write the selected setlist artifacts; one shared timestamp per call.

    Every requested format is rendered into *out_dir* under a single
    ``{project}_{YYYY-MM-DD_HHmmss}`` basename so the files are visibly one
    export run.  Reads the project only — the export never mutates it.

    Parameters
    ----------
    project : Project
        The project whose sections and tracks are exported.
    out_dir : Path
        Directory to write the artifacts into.
    formats : Collection[str]
        Subset of ``{"txt", "csv", "m3u8"}``.
    include_streaming_uris : bool
        When true, M3U8 entries may fall back to ``spotify:track:`` /
        ``tidal:track:`` URIs for tracks without a local file.
    now : datetime | None
        Timestamp source, injectable for tests; local time when ``None``.

    Returns
    -------
    SetlistExportResult
        The written artifact paths and any skipped tracks.

    Raises
    ------
    ValueError
        If *formats* contains an unknown format name.
    SetlistExportError
        If an artifact cannot be written; carries the failed ``path``.

    """
    unknown = sorted(set(formats) - {"txt", "csv", "m3u8"})
    if unknown:
        raise ValueError(f"Unknown setlist export format(s): {unknown}")
    # Seconds in the stamp keep same-minute re-exports from overwriting
    # the previous run's artifacts.
    stamp = (now or datetime.now(UTC).astimezone()).strftime("%Y-%m-%d_%H%M%S")
    base = f"{_safe_name(project.name)}_{stamp}"
    renderers = {
        "txt": _render_txt,
        "csv": _render_csv,
        "m3u8": lambda proj: _render_m3u8(proj, include_streaming_uris),
    }
    written: list[Path] = []
    skipped: list[SkippedItem] = []
    seen_skipped: set[tuple[str, str]] = set()
    for fmt in sorted(set(formats)):
        text, fmt_skipped = renderers[fmt](project)
        path = out_dir / f"{base}.{fmt}"
        try:
            if fmt == "csv":
                # csv.writer already terminates rows with \r\n; newline=""
                # disables text-mode translation that would turn each row
                # into \r\r\n on Windows.
                path.write_text(text, encoding="utf-8", newline="")
            else:
                path.write_text(text, encoding="utf-8")
        except OSError as exc:
            err = SetlistExportError(f"Could not write {path}: {exc}")
            err.path = path
            raise err from exc
        written.append(path)
        # Skips arrive per format per track; dedupe on (label, reason) so
        # the user-facing count describes distinct tracks, not render passes.
        for item in fmt_skipped:
            if (item.label, item.reason) not in seen_skipped:
                seen_skipped.add((item.label, item.reason))
                skipped.append(item)
    return SetlistExportResult(written=written, skipped=skipped)


def _render_txt(project: Project) -> tuple[str, list[SkippedItem]]:
    """Section headers plus continuously numbered track lines."""
    lines: list[str] = []
    skipped: list[SkippedItem] = []
    n = 0
    for section in project.sections:
        if lines:
            lines.append("")
        lines.append(_one_line(section.name))
        lines.append("")
        for tid in section.track_ids:
            track = project.get_track(tid)
            if track is None:
                skipped.append(SkippedItem(str(tid), "referenced track not in project"))
                continue
            n += 1
            line = f"{n}. {track.artist} – {track.title}{_txt_suffix(track)}"  # noqa: RUF001
            lines.append(_one_line(line))
    return "\n".join(lines) + "\n", skipped


def _txt_suffix(track: Track) -> str:
    """``(Key, BPM, M:SS)`` suffix assembled only from the fields present."""
    parts: list[str] = []
    if track.key:
        parts.append(track.key)
    if track.bpm is not None:
        parts.append(str(round(track.bpm)))
    if track.duration_formatted is not None:
        parts.append(track.duration_formatted)
    return f" ({', '.join(parts)})" if parts else ""


def _render_csv(project: Project) -> tuple[str, list[SkippedItem]]:
    """Full planning columns; ``track_no`` restarts at 1 inside each section."""
    skipped: list[SkippedItem] = []
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "section",
            "track_no",
            "artist",
            "title",
            "key",
            "bpm",
            "duration_s",
            "match_status",
            "filepath",
            "spotify_id",
            "tidal_id",
        ]
    )
    for section in project.sections:
        for track_no, tid in enumerate(section.track_ids, start=1):
            track = project.get_track(tid)
            if track is None:
                skipped.append(SkippedItem(str(tid), "referenced track not in project"))
                continue
            writer.writerow(
                [
                    section.name,
                    track_no,
                    track.artist,
                    track.title,
                    track.key,
                    track.bpm,
                    track.duration,
                    track.match_status.value,
                    track.filepath,
                    track.spotify_id,
                    track.tidal_id,
                ]
            )
    return buf.getvalue(), skipped


def _render_m3u8(project: Project, include_uris: bool) -> tuple[str, list[SkippedItem]]:
    """``#EXTM3U`` playlist; tracks with no playable path are skipped."""
    lines = ["#EXTM3U"]
    skipped: list[SkippedItem] = []
    for section in project.sections:
        for tid in section.track_ids:
            track = project.get_track(tid)
            if track is None:
                skipped.append(SkippedItem(str(tid), "referenced track not in project"))
                continue
            uri = _m3u_uri(track, include_uris)
            if uri is None:
                skipped.append(
                    SkippedItem(
                        track.display_name,
                        "no playable path or streaming URI",
                    )
                )
                continue
            secs = track.duration if track.duration is not None else -1
            lines.append(f"#EXTINF:{secs},{_one_line(track.display_name)}")
            lines.append(_one_line(uri))
    return "\n".join(lines) + "\n", skipped


def _m3u_uri(track: Track, include_uris: bool) -> str | None:
    """M3U path line: local file first, else a streaming URI when allowed."""
    if track.filepath:
        return track.filepath
    if include_uris:
        if track.spotify_id:
            return f"spotify:track:{track.spotify_id}"
        if track.tidal_id:
            return f"tidal:track:{track.tidal_id}"
    return None
