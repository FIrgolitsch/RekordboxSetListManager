# The UI

```
┌────────────────────────────────────────────────────────────────────────┐
│  Menu bar:  File  Edit  Project                                        │
├────────────────────────────────────────────────────────────────────────┤
│                                                                        │
│  Multi-section view (scrollable)                                       │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  ▶  Opener  (Aqua)          [Add track ▾]  [⋮]                  │  │
│  │  ─────────────────────────────────────────────────────────────── │  │
│  │  # │ Artist             │ Title             │ BPM │ Key │ Match  │  │
│  │  1 │ Bicep              │ Glue              │ 128 │ Am  │ ISRC   │  │
│  │  2 │ …                  │ …                 │ …   │ …   │ …      │  │
│  ├──────────────────────────────────────────────────────────────────┤  │
│  │  ▶  Build  (Yellow)                                               │  │
│  │  …                                                               │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                                                                        │
├────────────────────────────────────────────────────────────────────────┤
│  Bottom tabs:  Transition Note │ Match Info                            │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  Transition Note: [text editor]                                  │  │
│  └──────────────────────────────────────────────────────────────────┘  │
├────────────────────────────────────────────────────────────────────────┤
│  Status bar:  "2 sections · 18 tracks · 3 unmatched"                  │
└────────────────────────────────────────────────────────────────────────┘
```

---

## Menu bar

### File

| Menu item | Shortcut | Description |
|---|---|---|
| New | `Ctrl+N` | Create a blank project |
| Open… | `Ctrl+O` | Open a `.setmgr` file |
| Open Recent | — | Recently opened projects |
| Save | `Ctrl+S` | Save to the current file |
| Save As… | `Ctrl+Shift+S` | Save to a new file |
| Quit | `Ctrl+Q` | Exit the application |

### Edit

| Menu item | Shortcut | Description |
|---|---|---|
| Undo | `Ctrl+Z` | Undo the last change |
| Redo | `Ctrl+Y` | Redo the undone change |

### Project

| Menu item | Description |
|---|---|
| Export to Rekordbox… | Export the project to a Rekordbox XML file |
| Import from Streaming Service… | Import tracks from Spotify or Tidal |
| Service Settings… | Configure Spotify credentials and telemetry |
| Section Name Themes… | Create/edit section-name theme presets |
| Re-match with Rekordbox XML… | Re-run matching against a fresh Rekordbox XML export |
| Re-match with Rekordbox DB | Re-run matching against the local Rekordbox database |
| Manual Re-match… | Manually assign a local file to one or more tracks |

---

## Multi-section view

The central view lists all sections in order.  Each section is a collapsible block showing:

- **Header bar** — section name, type badge, colour swatch, and action buttons.
- **Track table** — rows for each track in the section.

### Track table columns

| Column | Description |
|---|---|
| `#` | Position within the section |
| Artist | Track artist(s) |
| Title | Track title |
| BPM | Tempo (from local Rekordbox metadata or streaming) |
| Key | Musical key |
| Duration | Track length |
| Match | Match strategy used (see [Rekordbox matching](rekordbox.md#matching)) |

**Sorting** — click any column header to sort; click again to reverse.

**Filtering** — type in the search box above the track table to filter by artist or title.

**Context menu** — right-click a track row for:

- Add track above / below
- Remove track
- Fix Match — manually reassign the local file
- Copy / Paste

---

## Bottom panels

### Transition Note

Freeform text attached to the *transition from the selected track to the next*.  Write cue points, key change notes, EQ hints, or anything you'd tell yourself mid-set.

Notes are saved with the project.  They are **not** exported to Rekordbox.

### Match Info

Read-only metadata panel showing the full details of the currently selected track:

- Match status (Matched / Unmatched / Manually Matched / Conflicted)
- Strategy used (ISRC, Exact, Fuzzy, Filename, None)
- Streaming metadata (artist, title, ISRC, source)
- Local Rekordbox metadata (file path, BPM, key, colour)

---

## Status bar

Shows a live summary: number of sections, total tracks, and how many are unmatched.

---

(undo-redo)=
## Undo / Redo

Rekordbox Set List Manager records a JSON snapshot after every mutation.  Up to 100 states are kept.  Undo/redo works for:

- Adding or removing sections or tracks
- Reordering tracks
- Changing section name, type, or colour
- Editing transition notes

Changes to settings and streaming auth are **not** undoable.
