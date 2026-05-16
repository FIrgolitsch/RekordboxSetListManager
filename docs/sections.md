# Sections

A **section** represents a phase of your DJ set — Opener, Warm Up, Build, and so on.  It has a name, a type, a colour, and an ordered list of tracks.

---

## Section types

The following types are built in.  Each type has a default Rekordbox track colour and a display label.

| Type | Label | Default colour | Hex |
|---|---|---|---|
| `opener` | Opener | Aqua | `#10E4DC` |
| `warm_up` | Warm Up | Green | `#1EE12B` |
| `build` | Build | Yellow | `#F8E550` |
| `peak` | Peak | Red | `#F87070` |
| `after_peak` | After Peak | Orange | `#FFA064` |
| `closing` | Closing | Blue | `#1E50F0` |
| `break` | Break | Purple | `#9828F0` |
| `general` | General | None (no colour) | — |

The colour mapping follows the natural energy arc of a set: cool tones for low-energy phases, warm tones for high-energy phases.

---

## Creating a section

1. Click **Add Section** (toolbar or section panel `+` button), or press `Ctrl+Shift+A`.
2. Enter a **name** (e.g. "Friday Peak").
3. Choose a **type** from the dropdown.
4. Click **OK**.

The section appears at the bottom of the list with the default colour for its type.

---

## Renaming a section

Double-click the section header, or right-click → **Rename**.

---

## Reordering sections

Drag the section header to a new position.

---

## Deleting a section

Right-click the section header → **Delete Section**.  All tracks inside the section are removed.  This action is undoable.

---

## Colours

Each section carries a **Rekordbox colour** that is embedded in the exported XML.  When Rekordbox imports the XML, tracks are shown in that colour in the browser and on CDJs that support colour tagging.

### Available colours

| Name | Hex |
|---|---|
| None (no colour) | — |
| Pink | `#F870F8` |
| Red | `#F87070` |
| Orange | `#FFA064` |
| Yellow | `#F8E550` |
| Green | `#1EE12B` |
| Aqua | `#10E4DC` |
| Blue | `#1E50F0` |
| Purple | `#9828F0` |

These are the exact colour codes baked into the Rekordbox 7 binary — no other colours are possible in a Rekordbox-compatible export.

### Changing a section's colour

Click the colour swatch in the section header to open the colour picker, then click the desired colour.

### Recolour all sections by type

**Project → Recolour All Sections** resets every section's colour to the default for its type.  Useful after changing section types in bulk.

---

## Name themes

A **name theme** is a saved mapping from section type to display name.  You can have multiple themes per project and apply one at a time to rename all sections in bulk.

**Example themes:**

| Type | Default | "Berlin Techno" theme | "Melodic House" theme |
|---|---|---|---|
| Opener | Opener | Eingang | Dawn |
| Build | Build | Aufbau | Journey |
| Peak | Peak | Höhepunkt | Climax |
| Closing | Closing | Ausgang | Dusk |

### Creating a theme

1. Open **Project → Section Name Themes…**
2. Click **New Theme** and enter a theme name.
3. Fill in the name overrides for each section type (leave blank to keep the default label).
4. Click **OK**.

### Applying a theme

In the Section Name Themes dialog, select a theme and click **Apply to Project**.  All sections whose type has a non-blank override will be renamed instantly.  The operation is undoable.
