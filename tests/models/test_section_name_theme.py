"""Tests for models/section_name_theme.py."""

import pytest

from rekordbox_set_list_manager.models.enums import SectionType
from rekordbox_set_list_manager.models.section_name_theme import SectionNameTheme

DAWN_THEME = {
    SectionType.OPENER: "Dawn",
    SectionType.WARM_UP: "Morning",
    SectionType.BUILD: "Midday",
    SectionType.PEAK: "Afternoon",
    SectionType.AFTER_PEAK: "Dusk",
    SectionType.CLOSING: "Night",
}


@pytest.fixture
def theme() -> SectionNameTheme:
    return SectionNameTheme(name="Dawn to Night", names=DAWN_THEME)


def test_theme_defaults():
    t = SectionNameTheme(name="Empty")
    assert t.names == {}


def test_display_name_for_mapped(theme):
    assert theme.display_name_for(SectionType.OPENER) == "Dawn"
    assert theme.display_name_for(SectionType.PEAK) == "Afternoon"
    assert theme.display_name_for(SectionType.CLOSING) == "Night"


def test_display_name_for_unmapped(theme):
    assert theme.display_name_for(SectionType.BREAK) is None
    assert theme.display_name_for(SectionType.GENERAL) is None


def test_partial_theme():
    t = SectionNameTheme(name="Partial", names={SectionType.PEAK: "Climax"})
    assert t.display_name_for(SectionType.PEAK) == "Climax"
    assert t.display_name_for(SectionType.OPENER) is None


def test_theme_json_round_trip(theme):
    json_str = theme.model_dump_json()
    restored = SectionNameTheme.model_validate_json(json_str)
    assert restored == theme
    assert restored.names[SectionType.OPENER] == "Dawn"
    assert restored.names[SectionType.CLOSING] == "Night"
