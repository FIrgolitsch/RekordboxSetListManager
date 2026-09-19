"""Tests for models/harmonic.py."""

import pytest

from rekordbox_set_list_manager.models.enums import KeyRelation
from rekordbox_set_list_manager.models.harmonic import (
    ParsedKey,
    format_key,
    key_relationship,
    parse_key,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Classic Rekordbox Tonality/KeyName spellings.
        ("Am", ParsedKey(8, "A")),
        ("C", ParsedKey(8, "B")),
        ("Bb", ParsedKey(6, "B")),
        ("F#m", ParsedKey(11, "A")),
        ("Db", ParsedKey(3, "B")),
        # Camelot codes (letter case-insensitive).
        ("8A", ParsedKey(8, "A")),
        ("12b", ParsedKey(12, "B")),
        # OpenKey spellings: n -> ((n+6) mod 12)+1; m->A ring, d->B ring.
        ("8d", ParsedKey(3, "B")),
        ("1m", ParsedKey(8, "A")),
        ("6d", ParsedKey(1, "B")),
        # Flats fold enharmonically.
        ("Ebm", ParsedKey(2, "A")),
    ],
)
def test_parse_key_known_spellings(text, expected):
    assert parse_key(text) == expected


@pytest.mark.parametrize("text", ["", None, "H", "X#", "13A", "8C"])
def test_parse_key_junk_returns_none(text):
    assert parse_key(text) is None


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("8A", "8A", KeyRelation.SAME),
        ("Am", "Am", KeyRelation.SAME),
        ("8A", "9A", KeyRelation.FIFTH_UP),
        ("8A", "7A", KeyRelation.FIFTH_DOWN),
        ("8A", "8B", KeyRelation.RELATIVE),
        ("8A", "10A", KeyRelation.ENERGY_UP),
        ("8A", "6A", KeyRelation.ENERGY_DOWN),
        ("8A", "3A", KeyRelation.SEMITONE_LIFT),
        # Clash: same letter, wheel delta not in the tier table.
        ("8A", "4A", None),
        # Clash: cross-letter at different wheel positions.
        ("8A", "9B", None),
        # Unparseable on either side -> no relationship.
        (None, "8A", None),
        ("8A", "zzz", None),
        (None, None, None),
    ],
)
def test_key_relationship(a, b, expected):
    assert key_relationship(a, b) == expected


def test_key_relationship_accepts_parsed_keys():
    a = parse_key("Am")
    b = parse_key("Em")
    assert key_relationship(a, b) == KeyRelation.FIFTH_UP


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Am", "Am (8A)"),
        ("C", "C (8B)"),
        (None, "—"),
        ("", "—"),
        # Unparseable keys render raw — never a fabricated code.
        ("zzz", "zzz"),
    ],
)
def test_format_key(text, expected):
    assert format_key(text) == expected
