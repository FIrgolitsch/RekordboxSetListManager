"""Tests for services/setlist_export.py."""

import csv
import io
from datetime import UTC, datetime

import pytest

from rekordbox_set_list_manager.models.project import Project
from rekordbox_set_list_manager.models.section import Section
from rekordbox_set_list_manager.models.track import Track
from rekordbox_set_list_manager.services.setlist_export import (
    SetlistExportError,
    _safe_name,
    export_setlist,
)

_NOW = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)


def test_txt_section_headers_and_continuous_numbering(project, tmp_path):
    extra = Track(title="Atlas", artist="Bicep", bpm=124.0, key="Em", duration=420)
    closing = Section(name="Closing")
    closing.add_track(extra.id)
    project.add_track(extra)
    project.add_section(closing)

    result = export_setlist(project, tmp_path, {"txt"}, now=_NOW)

    assert [p.name for p in result.written] == ["Summer Tour_2026-01-02_0304.txt"]
    assert result.skipped == []
    text = result.written[0].read_text(encoding="utf-8")
    assert "Peak" in text
    assert "Closing" in text
    assert "1. DJ Koze – Sundown (Am, 128, 6:30)" in text  # noqa: RUF001 — en-dash is the locked format
    assert "2. Bicep – Atlas (Em, 124, 7:00)" in text  # noqa: RUF001


def test_txt_suffix_omits_missing_fields(tmp_path):
    bare = Track(title="NoMeta", artist="Someone")
    proj = Project(name="P")
    proj.add_track(bare)
    sec = Section(name="S")
    sec.add_track(bare.id)
    proj.add_section(sec)

    result = export_setlist(proj, tmp_path, {"txt"}, now=_NOW)

    text = result.written[0].read_text(encoding="utf-8")
    assert "1. Someone – NoMeta\n" in text  # noqa: RUF001


def test_csv_header_and_per_section_numbering(project, tmp_path):
    extra = Track(title="Atlas", artist="Bicep", filepath="/music/atlas.mp3")
    closing = Section(name="Closing")
    closing.add_track(extra.id)
    project.add_track(extra)
    project.add_section(closing)

    result = export_setlist(project, tmp_path, {"csv"}, now=_NOW)

    rows = list(csv.reader(io.StringIO(result.written[0].read_text(encoding="utf-8"))))
    assert rows[0] == [
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
    assert rows[1][:4] == ["Peak", "1", "DJ Koze", "Sundown"]
    assert rows[1][4:7] == ["Am", "128.0", "390"]
    assert rows[1][7] == "unmatched"
    # track_no restarts at 1 inside each section
    assert rows[2][:2] == ["Closing", "1"]
    assert rows[2][8] == "/music/atlas.mp3"


def test_m3u8_extinf_and_filepath_line(project, track, tmp_path):
    track.filepath = "/music/sundown.mp3"

    result = export_setlist(project, tmp_path, {"m3u8"}, now=_NOW)

    assert result.skipped == []
    lines = result.written[0].read_text(encoding="utf-8").splitlines()
    assert lines[0] == "#EXTM3U"
    idx = lines.index("#EXTINF:390,DJ Koze - Sundown")
    assert lines[idx + 1] == "/music/sundown.mp3"


def test_m3u8_streaming_uri_fallback(tmp_path):
    spotify_only = Track(title="Atlas", artist="Bicep", spotify_id="sp-2")
    tidal_only = Track(title="Sundown", artist="DJ Koze", tidal_id="td-7", duration=240)
    proj = Project(name="P")
    proj.add_track(spotify_only)
    proj.add_track(tidal_only)
    sec = Section(name="S")
    sec.add_track(spotify_only.id)
    sec.add_track(tidal_only.id)
    proj.add_section(sec)

    result = export_setlist(proj, tmp_path, {"m3u8"}, now=_NOW)

    lines = result.written[0].read_text(encoding="utf-8").splitlines()
    assert lines[lines.index("#EXTINF:-1,Bicep - Atlas") + 1] == "spotify:track:sp-2"
    assert lines[lines.index("#EXTINF:240,DJ Koze - Sundown") + 1] == "tidal:track:td-7"
    assert result.skipped == []


def test_m3u8_track_without_path_or_uri_is_skipped(project, tmp_path):
    result = export_setlist(project, tmp_path, {"m3u8"}, now=_NOW)

    lines = result.written[0].read_text(encoding="utf-8").splitlines()
    assert lines == ["#EXTM3U"]
    assert len(result.skipped) == 1
    assert result.skipped[0].label == "DJ Koze - Sundown"
    assert "no playable path or streaming URI" in result.skipped[0].reason


def test_all_formats_share_one_timestamped_basename(project, tmp_path):
    result = export_setlist(project, tmp_path, {"txt", "csv", "m3u8"}, now=_NOW)

    assert sorted(p.name for p in result.written) == [
        "Summer Tour_2026-01-02_0304.csv",
        "Summer Tour_2026-01-02_0304.m3u8",
        "Summer Tour_2026-01-02_0304.txt",
    ]


def test_safe_name_strips_path_separators():
    cleaned = _safe_name("../a:b/c")
    for ch in '/\\:*?"<>|':
        assert ch not in cleaned


def test_export_to_missing_directory_raises_with_path(project, tmp_path):
    out_dir = tmp_path / "nonexistent" / "deeper"

    with pytest.raises(SetlistExportError) as exc_info:
        export_setlist(project, out_dir, {"txt"}, now=_NOW)

    assert exc_info.value.path == out_dir / "Summer Tour_2026-01-02_0304.txt"
    assert exc_info.value.__cause__ is not None
