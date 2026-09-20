# Rekordbox Set List Manager

[![CI](https://github.com/FIrgolitsch/RekordboxSetListManager/actions/workflows/ci.yml/badge.svg)](https://github.com/FIrgolitsch/RekordboxSetListManager/actions/workflows/ci.yml)

DJ set organizer with Rekordbox, Spotify, and Tidal integration.

Import playlists from streaming services, match tracks against your local Rekordbox library,
organize them into structured sections, and export to Rekordbox XML with section colors and
metadata preserved.

## Features

- **Streaming import** — load Spotify or Tidal playlists; tracks are matched automatically to your local library via ISRC, artist+title, and fuzzy search
- **Rekordbox collection** — auto-detect local Rekordbox DB or import via XML for matching
- **Set builder** — divide tracks into named sections (Opener, Build, Peak, Closer, …) with drag-and-drop reordering
- **Section colors** — apply colored labels that round-trip into Rekordbox as `Colour` metadata
- **Export to Rekordbox** — generates a valid DJ_PLAYLISTS XML (folder-per-section with section name in `Comments`)
- **Transition notes** — freeform text notes for each track-to-track transition, saved with the project
- **Dark theme** — Catppuccin Mocha-inspired QSS stylesheet

## Requirements

- Python 3.14+
- Rekordbox 6 or 7 (for XML import/export and DB auto-detection)
- Spotify account — optional, for playlist import
- Tidal account — optional, for playlist import

## Download

Pre-built binaries for macOS (Apple Silicon & Intel), Windows, and Linux are attached to each [GitHub Release](https://github.com/FIrgolitsch/RekordboxSetListManager/releases).

## Setup (from source)

```bash
# Clone
git clone https://github.com/FIrgolitsch/RekordboxSetListManager.git rekordbox_set_list_manager
cd rekordbox_set_list_manager

# Install uv (if needed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies
uv sync --group dev

# Run
uv run rslm

# Tests
uv run pytest

# Lint
uv run ruff check
```

## Configuration (Spotify)

Tidal authentication happens via a device-code login in the browser — click **Connect to Tidal…** in **Project → Service Settings…** or the Import dialog; no credentials are required. An optional `client_id`/`client_secret` override exists via the `SET_MANAGER_TIDAL_CLIENT_ID`/`SET_MANAGER_TIDAL_CLIENT_SECRET` environment variables or `config.json` — see [Connecting Streaming Services](docs/connecting-services.md#tidal) for details.

For Spotify you need a Client ID:

1. Go to [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard) and create an app.
2. Add `http://127.0.0.1:8888/callback` as a Redirect URI in the app settings.
3. In Rekordbox Set List Manager, open **Project → Service Settings…** and paste your Client ID.

## Usage

### Building a set

1. **File → New** to start a fresh project.
2. Click **+ Section** in the filter bar above the section list (or press **Ctrl+Shift+A**) to build the section structure.
3. Select a section, then right-click inside its track table (or press **Ctrl+T**) → **Add track…** to add tracks manually, or import from a streaming service (see below).
4. Drag rows to reorder tracks within a section.
5. Use the **filter bar** above the track table to narrow tracks by title or artist. Click column headers to sort.
6. Click a track to open the **Transition Note** editor below the table — write cues, key changes, or anything useful for the mix.
7. **File → Save** (`Ctrl+S`) — projects are saved as `.setmgr` files (JSON).

### Importing from Spotify or Tidal

1. **Project → Import from Streaming Service…**
2. Select the **Spotify** or **Tidal** tab and authenticate.
3. Browse your playlists and pick one.
4. Optionally load your Rekordbox collection (XML or auto-detected DB) to match streaming tracks to local files.
5. Click **Match** to run the matching engine, then **Import** to add the results.
6. Unmatched tracks can still be added as metadata-only entries.

### Exporting to Rekordbox

**Project → Export to Rekordbox…** → choose **Export to XML file** and pick an output `.xml` file, or **Export directly to Rekordbox database** when a local Rekordbox database is detected (Rekordbox must be closed; a backup is taken automatically).
For XML: in Rekordbox, set **Preferences → Advanced → Database → rekordbox xml → Imported Library** to the exported file, then right-click a playlist in the rekordbox xml tree and choose **Import Playlist**.
Each section appears as a sub-folder; track colors and comments carry over.

## Keyboard shortcuts

| Key | Action |
|-----|--------|
| `Ctrl+N` | New project |
| `Ctrl+O` | Open project |
| `Ctrl+S` | Save project |
| `Ctrl+Shift+S` | Save As |
| `Ctrl+Z` | Undo |
| `Ctrl+Y` | Redo |
| `Ctrl+Shift+A` | Add section |
| `Ctrl+T` | Add track to the selected section |
| `Ctrl+F` | Focus the track filter |
| `Delete` | Remove selected track from section |
| `Ctrl+Q` | Quit |

## Project file format

`.setmgr` files are JSON with a version envelope:

```json
{ "version": "1", "project": { … } }
```

Fields are versioned; opening files from older releases is backwards-compatible.

## Distribution (standalone binary)

Builds are produced automatically by GitHub Actions on each release. To build locally:

```bash
uv sync --all-extras
uv run pyinstaller rekordbox_set_list_manager.spec --noconfirm
# Output: dist/RekordboxSetListManager.app  (macOS)  /  dist/RekordboxSetListManager/  (all platforms)
```

## Architecture

Model-View-Service:

| Layer | Location | Notes |
|-------|----------|-------|
| Models | `src/rekordbox_set_list_manager/models/` | Pydantic v2, no Qt dependency |
| Services | `src/rekordbox_set_list_manager/services/` | Streaming APIs, Rekordbox XML/DB, matching |
| GUI | `src/rekordbox_set_list_manager/gui/` | PySide6; Qt item models bridge domain → views |
| Utils | `src/rekordbox_set_list_manager/utils/` | Config, constants, dark theme |

## License

GPL-3.0-or-later
