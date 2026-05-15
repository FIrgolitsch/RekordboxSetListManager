#!/usr/bin/env python
"""Verify Rekordbox XML colour values against the RekordboxColor enum.

Usage:
    uv run python scripts/verify_rb_colors.py <collection.xml>

Parses a real Rekordbox XML export and prints each track's colour as a hex
value alongside the matching enum member (or UNKNOWN if not in the enum).
Use this after exporting from a real Rekordbox installation to confirm that
the RekordboxColor enum values in models/enums.py are still correct.

Background
----------
In Rekordbox 7, the DjmdColor.ColorCode database column is always NULL.
The 8 track colours (Pink, Red, Orange, Yellow, Green, Aqua, Blue, Purple)
are hardcoded in the Rekordbox application binary.  Their 24-bit RGB values
are written verbatim as decimal integers into the `Colour` attribute of each
TRACK element in the exported XML.  No user configuration file stores RGB
values — settings files only reference a colour by its 1-based index.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# Allow running without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from set_manager.models.enums import RekordboxColor


def main(xml_path: Path) -> None:
    if not xml_path.exists():
        print(f"ERROR: file not found: {xml_path}", file=sys.stderr)
        sys.exit(1)

    tree = ET.parse(xml_path)
    root = tree.getroot()
    collection = root.find("COLLECTION")
    if collection is None:
        print("ERROR: no COLLECTION element found", file=sys.stderr)
        sys.exit(1)

    seen: dict[int, str] = {}  # colour_int → first track title
    mismatches: list[str] = []

    for elem in collection.findall("TRACK"):
        title = elem.get("Name", "").strip() or elem.get("Artist", "").strip() or "<unknown>"
        colour_str = elem.get("Colour", "0")
        try:
            colour_int = int(colour_str)
        except ValueError:
            print(f"  WARNING: non-integer Colour={colour_str!r} on {title!r}")
            continue

        if colour_int == 0:
            continue  # NONE — skip

        if colour_int not in seen:
            seen[colour_int] = title
            if colour_int in RekordboxColor._value2member_map_:
                member = RekordboxColor(colour_int)
                print(f"  OK  {colour_int:>10} = 0x{colour_int:06X}  {member.name:<8}  ({title!r})")
            else:
                msg = (
                    f"  !!  {colour_int:>10} = 0x{colour_int:06X}  UNKNOWN  ({title!r})"
                )
                print(msg)
                mismatches.append(msg)

    print()
    if mismatches:
        print(
            f"RESULT: {len(mismatches)} UNKNOWN colour(s) "
            "— update RekordboxColor in models/enums.py"
        )
        sys.exit(1)
    else:
        print(f"RESULT: all {len(seen)} non-zero colours match the enum  ✓")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <collection.xml>")
        sys.exit(1)
    main(Path(sys.argv[1]))
