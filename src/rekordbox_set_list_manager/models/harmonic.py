"""Harmonic key parsing and Camelot-wheel compatibility for DJ set planning."""

import re
from dataclasses import dataclass

from rekordbox_set_list_manager.models.enums import KeyRelation

# Note name -> semitone (C=0).  Flats fold enharmonically to sharps.
_SEMITONES = {
    "c": 0,
    "c#": 1,
    "db": 1,
    "d": 2,
    "d#": 3,
    "eb": 3,
    "e": 4,
    "f": 5,
    "f#": 6,
    "gb": 6,
    "g": 7,
    "g#": 8,
    "ab": 8,
    "a": 9,
    "a#": 10,
    "bb": 10,
    "b": 11,
}
_MINOR_WORDS = {"m", "min", "minor"}
_MAJ_WORDS = {"", "maj", "major"}

_CAMELOT_RE = re.compile(r"^([1-9]|1[0-2])([ABab])$")
_OPENKEY_RE = re.compile(r"^([1-9]|1[0-2])([mdMD])$")
_NOTE_RE = re.compile(r"^\s*([A-Ga-g])([#b♯♭]?)\s*([A-Za-z]*)\s*$")


@dataclass(frozen=True)
class ParsedKey:
    """A parsed musical key as Camelot wheel position (1-12) + ring ('A'|'B')."""

    number: int  # 1-12
    letter: str  # "A" (minor) or "B" (major)


def parse_key(text: str | None) -> ParsedKey | None:
    """Parse a Rekordbox/Camelot/OpenKey key string; None when unparseable.

    Accepts classic note spellings (``"Am"``, ``"Bb"``, ``"F#m"``), Camelot
    codes (``"8A"``, ``"12b"``), and OpenKey spellings (``"8d"``, ``"1m"``).
    Flat spellings fold enharmonically.  Never raises — junk or empty input
    returns None.

    Parameters
    ----------
    text : str or None
        The key string to parse (Rekordbox ``Tonality``/``KeyName`` or
        free-text user entry).

    Returns
    -------
    ParsedKey or None
        The Camelot wheel position, or None when *text* is unparseable.

    """
    if not text:
        return None
    s = text.strip()
    m = _CAMELOT_RE.match(s)
    if m:
        return ParsedKey(int(m.group(1)), m.group(2).upper())
    m = _OPENKEY_RE.match(s)
    if m:
        n = int(m.group(1))
        # OpenKey n -> Camelot ((n+6) mod 12)+1 ; m->A ring, d->B ring
        return ParsedKey((n + 6) % 12 + 1, "A" if m.group(2).lower() == "m" else "B")
    m = _NOTE_RE.match(s)
    if m:
        note = (m.group(1) + m.group(2)).lower().replace("♯", "#").replace("♭", "b")
        mode = m.group(3).lower()
        semitone = _SEMITONES.get(note)
        if semitone is None or mode not in _MINOR_WORDS | _MAJ_WORDS:
            return None
        return _semitone_to_camelot(semitone, mode in _MINOR_WORDS)
    return None


def _semitone_to_camelot(semitone: int, minor: bool) -> ParsedKey:
    # Minor ring: 1A=Ab(8); each +1 position = +7 semitones (fifths).
    # Major ring: 1B=B(11); same step.  Inverse of 7 mod 12 is 7.
    base = 8 if minor else 11
    number = ((semitone - base) * 7) % 12 + 1
    return ParsedKey(number, "A" if minor else "B")


def key_relationship(a: str | ParsedKey | None, b: str | ParsedKey | None) -> KeyRelation | None:
    """Return the tiered compatibility of playing *b* after *a*.

    Parameters
    ----------
    a : str, ParsedKey, or None
        The key of the track playing first (raw string or parsed key).
    b : str, ParsedKey, or None
        The key of the track playing second (raw string or parsed key).

    Returns
    -------
    KeyRelation or None
        The transition tier — direction a→b is preserved — or None for a
        clash or an unparseable key on either side (tri-state: None means
        "no badge", never a false clash).

    """
    ka = a if isinstance(a, ParsedKey) else parse_key(a)
    kb = b if isinstance(b, ParsedKey) else parse_key(b)
    if ka is None or kb is None:
        return None
    if ka == kb:
        return KeyRelation.SAME
    if ka.number == kb.number:  # A<->B at the same position = relative
        return KeyRelation.RELATIVE
    if ka.letter != kb.letter:
        return None  # cross-letter at different positions = clash
    d = (kb.number - ka.number) % 12
    return {
        1: KeyRelation.FIFTH_UP,
        11: KeyRelation.FIFTH_DOWN,
        2: KeyRelation.ENERGY_UP,
        10: KeyRelation.ENERGY_DOWN,
        7: KeyRelation.SEMITONE_LIFT,
    }.get(d)


def format_key(text: str | None) -> str:
    """Format *text* as ``"Am (8A)"`` — Rekordbox spelling plus Camelot.

    Parameters
    ----------
    text : str or None
        The stored key string.

    Returns
    -------
    str
        ``"{text} ({Camelot})"`` when parseable, ``"—"`` when *text* is
        falsy, otherwise the raw *text* unchanged.

    """
    if not text:
        return "—"
    pk = parse_key(text)
    return f"{text} ({pk.number}{pk.letter})" if pk else text


# Grading tiers: green = mix-safe, amber = energy move, absent = clash/unknown.
_SAFE = {
    KeyRelation.SAME,
    KeyRelation.FIFTH_UP,
    KeyRelation.FIFTH_DOWN,
    KeyRelation.RELATIVE,
}
_ENERGY = {
    KeyRelation.ENERGY_UP,
    KeyRelation.ENERGY_DOWN,
    KeyRelation.SEMITONE_LIFT,
}
