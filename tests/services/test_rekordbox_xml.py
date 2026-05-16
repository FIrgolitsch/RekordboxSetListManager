"""Tests for services/rekordbox_xml.py."""

from __future__ import annotations

import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from rekordbox_set_list_manager.models.enums import (
    MatchStatus,
    RekordboxColor,
    SectionType,
    TrackSource,
)
from rekordbox_set_list_manager.models.section import Section
from rekordbox_set_list_manager.models.track import Track
from rekordbox_set_list_manager.services.rekordbox_xml import (
    RekordboxXmlError,
    RekordboxXmlService,
    _filepath_to_location,
    _location_to_filepath,
)

FIXTURE_XML = Path(__file__).parent.parent / "fixtures" / "rekordbox_collection.xml"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def service() -> RekordboxXmlService:
    return RekordboxXmlService()


@pytest.fixture
def track_a() -> Track:
    return Track(
        title="Sundown",
        artist="DJ Koze",
        bpm=128.0,
        key="Am",
        duration=390,
        filepath="/Users/dj/Music/sundown.mp3",
    )


@pytest.fixture
def track_b() -> Track:
    return Track(
        title="Pick Up",
        artist="DJ Koze",
        bpm=125.5,
        key="Fm",
        duration=420,
        filepath="/Users/dj/Music/pick_up.mp3",
        color=RekordboxColor.BLUE,  # track-level color override
    )


@pytest.fixture
def two_section_fixture(track_a: Track, track_b: Track) -> tuple[list[Section], str, dict]:
    """Two sections (Peak + After Peak) with their tracks, and a set name."""
    peak = Section(name="Peak", section_type=SectionType.PEAK, color=RekordboxColor.RED)
    peak.add_track(track_a.id)

    after = Section(
        name="After Peak", section_type=SectionType.AFTER_PEAK, color=RekordboxColor.AQUA
    )
    after.add_track(track_b.id)

    tracks = {track_a.id: track_a, track_b.id: track_b}
    return [peak, after], "Berghain 2024-06-01", tracks


# ---------------------------------------------------------------------------
# export_set — structure
# ---------------------------------------------------------------------------


class TestExportSet:
    def test_creates_valid_xml_file(
        self, service: RekordboxXmlService, two_section_fixture, tmp_path: Path
    ) -> None:
        sections, name, tracks = two_section_fixture
        out = tmp_path / "export.xml"
        service.export_set(sections, name, tracks, out)
        assert out.exists()
        root = ET.parse(out).getroot()
        assert root.tag == "DJ_PLAYLISTS"

    def test_xml_declaration_present(
        self, service: RekordboxXmlService, two_section_fixture, tmp_path: Path
    ) -> None:
        sections, name, tracks = two_section_fixture
        out = tmp_path / "export.xml"
        service.export_set(sections, name, tracks, out)
        content = out.read_text(encoding="utf-8")
        assert content.startswith("<?xml version")
        assert 'encoding="UTF-8"' in content

    def test_collection_entry_count(
        self, service: RekordboxXmlService, two_section_fixture, tmp_path: Path
    ) -> None:
        sections, name, tracks = two_section_fixture
        out = tmp_path / "export.xml"
        service.export_set(sections, name, tracks, out)
        root = ET.parse(out).getroot()
        collection = root.find("COLLECTION")
        assert collection is not None
        assert collection.get("Entries") == "2"
        assert len(collection.findall("TRACK")) == 2

    def test_track_metadata_in_collection(
        self,
        service: RekordboxXmlService,
        track_a: Track,
        two_section_fixture,
        tmp_path: Path,
    ) -> None:
        sections, name, tracks = two_section_fixture
        out = tmp_path / "export.xml"
        service.export_set(sections, name, tracks, out)
        root = ET.parse(out).getroot()
        collection = root.find("COLLECTION")
        assert collection is not None
        first = collection.findall("TRACK")[0]
        assert first.get("Name") == "Sundown"
        assert first.get("Artist") == "DJ Koze"
        assert first.get("AverageBpm") == "128.00"
        assert first.get("Tonality") == "Am"
        assert first.get("TotalTime") == "390"

    def test_section_name_written_to_comments(
        self, service: RekordboxXmlService, two_section_fixture, tmp_path: Path
    ) -> None:
        sections, name, tracks = two_section_fixture
        out = tmp_path / "export.xml"
        service.export_set(sections, name, tracks, out)
        root = ET.parse(out).getroot()
        collection = root.find("COLLECTION")
        assert collection is not None
        track_elems = collection.findall("TRACK")
        comments = {e.get("Name"): e.get("Comments") for e in track_elems}
        assert comments["Sundown"] == "Peak"
        assert comments["Pick Up"] == "After Peak"

    def test_section_color_applied_to_track(
        self,
        service: RekordboxXmlService,
        track_a: Track,
        two_section_fixture,
        tmp_path: Path,
    ) -> None:
        sections, name, tracks = two_section_fixture
        out = tmp_path / "export.xml"
        service.export_set(sections, name, tracks, out)
        root = ET.parse(out).getroot()
        collection = root.find("COLLECTION")
        assert collection is not None
        # track_a has no color override; should use section color (RED)
        sundown = next(e for e in collection.findall("TRACK") if e.get("Name") == "Sundown")
        assert sundown.get("Colour") == str(int(RekordboxColor.RED))

    def test_track_color_override_takes_precedence(
        self,
        service: RekordboxXmlService,
        track_b: Track,
        two_section_fixture,
        tmp_path: Path,
    ) -> None:
        sections, name, tracks = two_section_fixture
        out = tmp_path / "export.xml"
        service.export_set(sections, name, tracks, out)
        root = ET.parse(out).getroot()
        collection = root.find("COLLECTION")
        assert collection is not None
        # track_b.color = BLUE, section color is AQUA; BLUE should win
        pick_up = next(e for e in collection.findall("TRACK") if e.get("Name") == "Pick Up")
        assert pick_up.get("Colour") == str(int(RekordboxColor.BLUE))

    def test_playlists_folder_structure(
        self, service: RekordboxXmlService, two_section_fixture, tmp_path: Path
    ) -> None:
        sections, name, tracks = two_section_fixture
        out = tmp_path / "export.xml"
        service.export_set(sections, name, tracks, out)
        root = ET.parse(out).getroot()
        # ROOT > set folder > section playlists
        root_node = root.find("./PLAYLISTS/NODE")
        assert root_node is not None
        assert root_node.get("Name") == "ROOT"
        set_node = root_node.find("NODE")
        assert set_node is not None
        assert set_node.get("Name") == "Berghain 2024-06-01"
        assert set_node.get("Type") == "0"  # folder
        section_nodes = set_node.findall("NODE")
        assert len(section_nodes) == 2
        assert section_nodes[0].get("Name") == "Peak"
        assert section_nodes[0].get("Type") == "1"  # playlist
        assert section_nodes[1].get("Name") == "After Peak"

    def test_playlist_track_keys_match_collection_ids(
        self, service: RekordboxXmlService, two_section_fixture, tmp_path: Path
    ) -> None:
        sections, name, tracks = two_section_fixture
        out = tmp_path / "export.xml"
        service.export_set(sections, name, tracks, out)
        root = ET.parse(out).getroot()
        collection_ids = {e.get("TrackID") for e in root.findall("./COLLECTION/TRACK")}
        playlist_keys = {e.get("Key") for e in root.findall("./PLAYLISTS/NODE/NODE/NODE/TRACK")}
        assert playlist_keys.issubset(collection_ids)

    def test_filepath_written_as_location(
        self, service: RekordboxXmlService, two_section_fixture, tmp_path: Path
    ) -> None:
        sections, name, tracks = two_section_fixture
        out = tmp_path / "export.xml"
        service.export_set(sections, name, tracks, out)
        root = ET.parse(out).getroot()
        collection = root.find("COLLECTION")
        assert collection is not None
        sundown = next(e for e in collection.findall("TRACK") if e.get("Name") == "Sundown")
        assert sundown.get("Location") == "file://localhost/Users/dj/Music/sundown.mp3"

    def test_orphaned_track_ids_skipped(self, service: RekordboxXmlService, tmp_path: Path) -> None:
        """Track IDs in sections that aren't in the tracks dict are silently skipped."""
        section = Section(name="Peak", section_type=SectionType.PEAK, color=RekordboxColor.RED)
        section.add_track(uuid.uuid4())  # orphan — not in tracks dict

        out = tmp_path / "export.xml"
        service.export_set([section], "Test Set", {}, out)
        root = ET.parse(out).getroot()
        collection = root.find("COLLECTION")
        assert collection is not None
        assert len(collection.findall("TRACK")) == 0

    def test_export_raises_on_unwritable_path(
        self, service: RekordboxXmlService, two_section_fixture
    ) -> None:
        sections, name, tracks = two_section_fixture
        with pytest.raises(RekordboxXmlError, match="Could not write"):
            service.export_set(sections, name, tracks, Path("/nonexistent_dir/out.xml"))


# ---------------------------------------------------------------------------
# import_collection — reads tracks from XML
# ---------------------------------------------------------------------------


class TestImportCollection:
    def test_imports_from_fixture(self, service: RekordboxXmlService) -> None:
        tracks = service.import_collection(FIXTURE_XML)
        assert len(tracks) == 3

    def test_track_fields_parsed(self, service: RekordboxXmlService) -> None:
        tracks = service.import_collection(FIXTURE_XML)
        koze = next(t for t in tracks if t.title == "Sundown")
        assert koze.artist == "DJ Koze"
        assert koze.bpm == 128.0
        assert koze.key == "Am"
        assert koze.duration == 390
        assert koze.rekordbox_id == 1
        assert koze.source == TrackSource.REKORDBOX
        assert koze.match_status == MatchStatus.UNMATCHED

    def test_color_parsed_from_colour_attribute(self, service: RekordboxXmlService) -> None:
        tracks = service.import_collection(FIXTURE_XML)
        sundown = next(t for t in tracks if t.title == "Sundown")
        assert sundown.color == RekordboxColor.RED

    def test_zero_colour_parsed_as_none_enum_value(self, service: RekordboxXmlService) -> None:
        tracks = service.import_collection(FIXTURE_XML)
        edge = next(t for t in tracks if t.title == "Edge Track")
        assert edge.color == RekordboxColor.NONE  # Colour="0" maps to RekordboxColor.NONE
        assert edge.bpm is None
        assert edge.duration is None

    def test_filepath_parsed_from_location(self, service: RekordboxXmlService) -> None:
        tracks = service.import_collection(FIXTURE_XML)
        sundown = next(t for t in tracks if t.title == "Sundown")
        assert sundown.filepath == "/Users/dj/Music/dj_koze_sundown.mp3"

    def test_tracks_without_location_are_skipped(self, service: RekordboxXmlService) -> None:
        """Tracks with an empty Location (e.g. streaming-service entries) are excluded."""
        tracks = service.import_collection(FIXTURE_XML)
        titles = [t.title for t in tracks]
        assert "Intro Track" not in titles

    def test_streaming_url_location_is_skipped(
        self, service: RekordboxXmlService, tmp_path: Path
    ) -> None:
        """Tracks with a streaming-service URL (tidal://, etc.) are excluded."""
        xml_content = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<DJ_PLAYLISTS Version="1.0.0"><COLLECTION Entries="2">'
            '<TRACK TrackID="1" Name="Tidal Track" Artist="Artist"'
            ' AverageBpm="128.00" TotalTime="240"'
            ' Location="tidal://tracks/12345678" Tonality="" Colour="0"/>'
            '<TRACK TrackID="2" Name="Local Track" Artist="Artist"'
            ' AverageBpm="128.00" TotalTime="240"'
            ' Location="file://localhost/music/local.mp3" Tonality="" Colour="0"/>'
            "</COLLECTION></DJ_PLAYLISTS>"
        )
        f = tmp_path / "mixed.xml"
        f.write_text(xml_content, encoding="utf-8")
        tracks = service.import_collection(f)
        assert len(tracks) == 1
        assert tracks[0].title == "Local Track"

    def test_missing_file_raises(self, service: RekordboxXmlService, tmp_path: Path) -> None:
        with pytest.raises(RekordboxXmlError, match="File not found"):
            service.import_collection(tmp_path / "missing.xml")

    def test_invalid_xml_raises(self, service: RekordboxXmlService, tmp_path: Path) -> None:
        bad = tmp_path / "bad.xml"
        bad.write_text("not xml at all {{{{", encoding="utf-8")
        with pytest.raises(RekordboxXmlError, match="Invalid XML"):
            service.import_collection(bad)

    def test_wrong_root_element_raises(self, service: RekordboxXmlService, tmp_path: Path) -> None:
        wrong = tmp_path / "wrong.xml"
        wrong.write_text('<?xml version="1.0"?><LIBRARY></LIBRARY>', encoding="utf-8")
        with pytest.raises(RekordboxXmlError, match="Not a Rekordbox XML"):
            service.import_collection(wrong)

    def test_missing_collection_returns_empty(
        self, service: RekordboxXmlService, tmp_path: Path
    ) -> None:
        no_collection = tmp_path / "no_collection.xml"
        no_collection.write_text(
            '<?xml version="1.0"?><DJ_PLAYLISTS Version="1.0.0"></DJ_PLAYLISTS>',
            encoding="utf-8",
        )
        assert service.import_collection(no_collection) == []

    def test_tracks_without_name_and_artist_skipped(
        self, service: RekordboxXmlService, tmp_path: Path
    ) -> None:
        xml_content = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<DJ_PLAYLISTS Version="1.0.0">'
            '<COLLECTION Entries="1">'
            '<TRACK TrackID="1" Name="" Artist="" AverageBpm="0.00" TotalTime="0"'
            ' Colour="0" Location="" Tonality=""/>'
            "</COLLECTION>"
            "</DJ_PLAYLISTS>"
        )
        f = tmp_path / "empty_track.xml"
        f.write_text(xml_content, encoding="utf-8")
        assert service.import_collection(f) == []


# ---------------------------------------------------------------------------
# Round-trip: export then import
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_exported_tracks_can_be_re_imported(
        self,
        service: RekordboxXmlService,
        track_a: Track,
        two_section_fixture,
        tmp_path: Path,
    ) -> None:
        sections, name, tracks = two_section_fixture
        out = tmp_path / "round_trip.xml"
        service.export_set(sections, name, tracks, out)
        imported = service.import_collection(out)
        assert len(imported) == 2
        titles = {t.title for t in imported}
        assert titles == {"Sundown", "Pick Up"}

    def test_bpm_preserved_in_round_trip(
        self, service: RekordboxXmlService, two_section_fixture, tmp_path: Path
    ) -> None:
        sections, name, tracks = two_section_fixture
        out = tmp_path / "round_trip.xml"
        service.export_set(sections, name, tracks, out)
        imported = service.import_collection(out)
        sundown = next(t for t in imported if t.title == "Sundown")
        assert sundown.bpm == 128.0

    def test_key_preserved_in_round_trip(
        self, service: RekordboxXmlService, two_section_fixture, tmp_path: Path
    ) -> None:
        sections, name, tracks = two_section_fixture
        out = tmp_path / "round_trip.xml"
        service.export_set(sections, name, tracks, out)
        imported = service.import_collection(out)
        sundown = next(t for t in imported if t.title == "Sundown")
        assert sundown.key == "Am"


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


class TestFilepathHelpers:
    def test_absolute_path_to_location(self) -> None:
        assert _filepath_to_location("/Users/dj/track.mp3") == "file://localhost/Users/dj/track.mp3"

    def test_empty_filepath_to_location(self) -> None:
        assert _filepath_to_location(None) == ""
        assert _filepath_to_location("") == ""

    def test_location_to_filepath_localhost(self) -> None:
        assert _location_to_filepath("file://localhost/Users/dj/track.mp3") == "/Users/dj/track.mp3"

    def test_location_to_filepath_triple_slash(self) -> None:
        assert _location_to_filepath("file:///Users/dj/track.mp3") == "/Users/dj/track.mp3"

    def test_empty_location_gives_none(self) -> None:
        assert _location_to_filepath("") is None
        assert _location_to_filepath(None) is None


# ---------------------------------------------------------------------------
# Phase I — Rekordbox 7.2 colour regression
# ---------------------------------------------------------------------------

FIXTURE_RB72_COLORS = Path(__file__).parent.parent / "fixtures" / "rekordbox72_colors.xml"

_EXPECTED_COLORS = [
    ("Pink Track", RekordboxColor.PINK),
    ("Red Track", RekordboxColor.RED),
    ("Orange Track", RekordboxColor.ORANGE),
    ("Yellow Track", RekordboxColor.YELLOW),
    ("Green Track", RekordboxColor.GREEN),
    ("Aqua Track", RekordboxColor.AQUA),
    ("Blue Track", RekordboxColor.BLUE),
    ("Purple Track", RekordboxColor.PURPLE),
]


class TestRekordbox72Colors:
    """Regression tests for Rekordbox 7.2 XML colour codes.

    The fixture uses Colour integers derived from the RekordboxColor enum values.
    DjmdColor.ColorCode is always NULL in RB7; the XML Colour attribute is the
    authoritative source.
    """

    def test_fixture_exists(self) -> None:
        assert FIXTURE_RB72_COLORS.exists(), "rekordbox72_colors.xml fixture missing"

    def test_all_eight_colours_parsed(self, service: RekordboxXmlService) -> None:
        tracks = service.import_collection(FIXTURE_RB72_COLORS)
        assert len(tracks) == 8

    def test_each_colour_maps_to_correct_enum(self, service: RekordboxXmlService) -> None:
        tracks = service.import_collection(FIXTURE_RB72_COLORS)
        by_title = {t.title: t.color for t in tracks}
        for title, expected_color in _EXPECTED_COLORS:
            assert by_title[title] == expected_color, (
                f"{title}: expected {expected_color.name} ({expected_color.value:#08x}), "
                f"got {by_title[title]!r}"
            )

    def test_enum_covers_all_non_zero_rb7_colours(self) -> None:
        """RekordboxColor must contain exactly the 8 non-NONE RB7 colours."""
        non_none = [c for c in RekordboxColor if c != RekordboxColor.NONE]
        assert len(non_none) == 8
