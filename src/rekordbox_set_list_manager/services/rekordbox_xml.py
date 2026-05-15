"""Rekordbox XML export and import service.

Generates and parses the DJ_PLAYLISTS XML format accepted by Rekordbox 7.x.
Reference: https://cdn.rekordbox.com/files/20200410160904/xml_format_list.pdf
"""

from __future__ import annotations

import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import TYPE_CHECKING

from rekordbox_set_list_manager.models.enums import MatchStatus, RekordboxColor, TrackSource
from rekordbox_set_list_manager.models.track import Track

if TYPE_CHECKING:
    from uuid import UUID

    from rekordbox_set_list_manager.models.section import Section

_RB_XML_VERSION = "1.0.0"
_PRODUCT_NAME = "rekordbox"
_PRODUCT_VERSION = "7.0.0"
_PRODUCT_COMPANY = "AlphaTheta"


class RekordboxXmlError(Exception):
    """Raised when a Rekordbox XML file cannot be read or written."""


# ---------------------------------------------------------------------------
# Public service
# ---------------------------------------------------------------------------


class RekordboxXmlService:
    """Export a project's sections to Rekordbox XML and import Rekordbox XML track collections."""

    def export_set(
        self,
        sections: list[Section],
        name: str,
        tracks: dict[UUID, Track],
        path: Path,
    ) -> None:
        """Write the project's *sections* to a Rekordbox-importable XML file at *path*.

        Structure:
        - COLLECTION contains every track that appears in the exported set.
        - PLAYLISTS contains one top-level folder named after the set.
          Each section becomes a nested playlist folder inside that folder.
        - Each track's ``Colour`` is taken from its section color (or the
          track's own ``color`` override if set).
        - The section name is written into each track's ``Comments`` field.
        """
        root = ET.Element("DJ_PLAYLISTS", Version=_RB_XML_VERSION)
        ET.SubElement(
            root,
            "PRODUCT",
            Name=_PRODUCT_NAME,
            Version=_PRODUCT_VERSION,
            Company=_PRODUCT_COMPANY,
        )

        # Collect unique track IDs in set order, skipping any orphaned refs.
        ordered_ids: list[UUID] = []
        seen: set[UUID] = set()
        for section in sections:
            for tid in section.track_ids:
                if tid not in seen and tid in tracks:
                    ordered_ids.append(tid)
                    seen.add(tid)

        # Sequential 1-based TrackID map (Rekordbox requires integer IDs).
        id_map: dict[UUID, int] = {tid: i + 1 for i, tid in enumerate(ordered_ids)}

        # Per-track section metadata used for Colour and Comments.
        track_meta: dict[UUID, tuple[str, int]] = {}
        for section in sections:
            colour = int(section.color)
            for tid in section.track_ids:
                track_meta[tid] = (section.name, colour)

        # COLLECTION ----------------------------------------------------------
        collection = ET.SubElement(root, "COLLECTION", Entries=str(len(ordered_ids)))
        for tid in ordered_ids:
            track = tracks[tid]
            section_name, section_colour = track_meta.get(tid, ("", 0))
            effective_colour = int(track.color) if track.color is not None else section_colour
            collection.append(
                _track_to_elem(track, id_map[tid], section_name, effective_colour)
            )

        # PLAYLISTS -----------------------------------------------------------
        playlists_root = ET.SubElement(root, "PLAYLISTS")
        root_node = ET.SubElement(
            playlists_root, "NODE", Type="0", Name="ROOT", Count="1"
        )
        set_node = ET.SubElement(
            root_node,
            "NODE",
            Type="0",
            Name=name,
            Count=str(len(sections)),
        )
        for section in sections:
            section_ids = [tid for tid in section.track_ids if tid in tracks]
            playlist_node = ET.SubElement(
                set_node,
                "NODE",
                Type="1",
                Name=section.name,
                KeyType="0",
                Entries=str(len(section_ids)),
            )
            for tid in section_ids:
                ET.SubElement(playlist_node, "TRACK", Key=str(id_map[tid]))

        try:
            path.write_text(_pretty_xml(root), encoding="utf-8")
        except OSError as exc:
            raise RekordboxXmlError(f"Could not write XML to {path}: {exc}") from exc

    def import_collection(self, path: Path) -> list[Track]:
        """Parse *path* and return all COLLECTION tracks as :class:`Track` objects.

        Raises :class:`RekordboxXmlError` if the file is missing or malformed.
        """
        if not path.exists():
            raise RekordboxXmlError(f"File not found: {path}")
        try:
            tree = ET.parse(path)
        except ET.ParseError as exc:
            raise RekordboxXmlError(f"Invalid XML in {path}: {exc}") from exc

        root = tree.getroot()
        if root.tag != "DJ_PLAYLISTS":
            raise RekordboxXmlError(
                f"Not a Rekordbox XML file: root element is <{root.tag}>"
            )

        collection = root.find("COLLECTION")
        if collection is None:
            return []

        return [
            t for elem in collection.findall("TRACK") if (t := _elem_to_track(elem)) is not None
        ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bpm_str(bpm: float | None) -> str:
    return f"{bpm:.2f}" if bpm is not None else "0.00"


def _filepath_to_location(filepath: str | None) -> str:
    """Convert a local file path to a Rekordbox ``Location`` URL."""
    if not filepath:
        return ""
    p = Path(filepath)
    if p.is_absolute():
        return "file://localhost" + urllib.parse.quote(filepath, safe="/")
    return filepath


def _location_to_filepath(location: str | None) -> str | None:
    """Convert a Rekordbox ``Location`` URL to a plain file path."""
    if not location:
        return None
    if location.startswith("file://localhost"):
        return urllib.parse.unquote(location[len("file://localhost"):])
    if location.startswith("file:///"):
        return urllib.parse.unquote("/" + location[len("file:///"):])
    return location or None


def _track_to_elem(track: Track, track_id: int, section_name: str, colour: int) -> ET.Element:
    elem = ET.Element("TRACK")
    elem.set("TrackID", str(track_id))
    elem.set("Name", track.title)
    elem.set("Artist", track.artist)
    elem.set("Album", "")
    elem.set("Genre", "")
    elem.set("Kind", "")
    elem.set("Size", "0")
    elem.set("TotalTime", str(track.duration or 0))
    elem.set("DiscNumber", "0")
    elem.set("TrackNumber", "0")
    elem.set("Year", "")
    elem.set("AverageBpm", _bpm_str(track.bpm))
    elem.set("DateAdded", "")
    elem.set("BitRate", "0")
    elem.set("SampleRate", "44100")
    elem.set("Comments", section_name)
    elem.set("PlayCount", "0")
    elem.set("Rating", "0")
    elem.set("Location", _filepath_to_location(track.filepath))
    elem.set("Remixer", "")
    elem.set("Tonality", track.key or "")
    elem.set("Label", "")
    elem.set("Mix", "")
    elem.set("Colour", str(colour))
    return elem


def _elem_to_track(elem: ET.Element) -> Track | None:
    """Convert a TRACK element to a local-file :class:`Track`.

    Returns ``None`` if the element lacks both name and artist, or if it has no
    local file path (e.g. streaming-service tracks with an empty Location).
    """
    name = (elem.get("Name") or "").strip()
    artist = (elem.get("Artist") or "").strip()
    if not name and not artist:
        return None

    filepath = _location_to_filepath(elem.get("Location", ""))
    if not filepath or "://" in filepath:
        return None

    rb_id_str = elem.get("TrackID", "")
    rekordbox_id = int(rb_id_str) if rb_id_str.isdigit() else None

    bpm_str = elem.get("AverageBpm", "0")
    try:
        bpm: float | None = float(bpm_str) or None
    except ValueError:
        bpm = None

    duration_str = elem.get("TotalTime", "0")
    try:
        duration: int | None = int(duration_str) or None
    except ValueError:
        duration = None

    colour_str = elem.get("Colour", "0")
    try:
        colour_int = int(colour_str)
        color: RekordboxColor | None = (
            RekordboxColor(colour_int)
            if colour_int in RekordboxColor._value2member_map_
            else None
        )
    except ValueError:
        color = None

    return Track(
        title=name,
        artist=artist,
        bpm=bpm,
        key=elem.get("Tonality") or None,
        duration=duration,
        filepath=filepath,
        rekordbox_id=rekordbox_id,
        source=TrackSource.REKORDBOX,
        match_status=MatchStatus.UNMATCHED,
        color=color,
    )


def _pretty_xml(root: ET.Element) -> str:
    """Return a valid, pretty-printed XML string with UTF-8 declaration."""
    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    return f'<?xml version="1.0" encoding="UTF-8"?>\n{body}\n'
