# Quick Start

This guide gets you from a blank project to an exported Rekordbox playlist in about five minutes.

---

## 1 — Import tracks from Spotify

1. Open **Project → Import from Streaming Service…**
2. Select the **Spotify** tab and click **Connect**.  A browser window opens — log in and approve access.
3. Choose a playlist from the dropdown and click **Import**.

Each track is fetched and immediately matched against your local Rekordbox library (see [Rekordbox matching](rekordbox.md#matching)).

> **No Spotify?**  You can add tracks manually instead — see [Tracks → Add a track manually](tracks.md#add-a-track-manually).

---

## 2 — Create sections

A section groups related tracks and maps to a phase of your set (Opener, Build, Peak, etc.).

1. Click **Add Section** in the toolbar, or use the **+** button in the section panel.
2. Give it a name and pick a **Section Type** from the dropdown (e.g. *Peak*).
3. Click **OK**.  The section appears in the main view with its default colour.

Repeat for as many sections as you need.  You can reorder sections by dragging their header.

---

## 3 — Arrange tracks

Drag any track from one section to another, or drag to reorder within a section.

To move a track to a specific position:

1. Select the track row.
2. Drag it to the target row in the same or another section.

---

## 4 — Review matches

Tracks are colour-coded by match status:

| Indicator | Meaning |
|---|---|
| No highlight | Matched — local file found |
| Orange text | Unmatched — no local file found yet |

Click a track to see full match details in the **Match Info** panel on the right.

If a track is wrong or unmatched, right-click it and choose **Fix Match** to browse your Rekordbox library manually.

---

## 5 — Export to Rekordbox

1. Open **Project → Export to Rekordbox…**
2. Choose a save location.
3. Open Rekordbox and import the exported XML via **File → Import Library XML**.

Your sections appear as separate playlists inside a folder named after your project.  Each track carries the section's colour as its Rekordbox memory cue colour.

---

## Next steps

- [Add transition notes](transitions.md) between tracks
- [Customise section colours](sections.md#colours)
- [Create name themes](sections.md#name-themes) to rename section types per project
- {ref}`Undo / redo <undo-redo>` any changes
