# Rekordbox Set List Manager

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
- Rekordbox 7.x (for XML import/export and DB auto-detection)
- Spotify account — optional, for playlist import
- Tidal account — optional, for playlist import

## Setup

```bash
# Clone
git clone <repo-url> set_manager
cd set_manager

# Create virtualenv (using pyenv)
pyenv virtualenv 3.14.2 set_manager
pyenv local set_manager

# Install (development mode)
pip install -e ".[dev]"

# Run
python -m set_manager        # or: set-manager

# Tests
pytest

# Lint
ruff check src/
```

## Configuration (Spotify)

Tidal authentication happens via a device-code login in the browser — no configuration needed.

For Spotify you need a Client ID:

1. Go to [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard) and create an app.
2. Add `http://127.0.0.1:8888/callback` as a Redirect URI in the app settings.
3. In Rekordbox Set List Manager, open **Project → Service Settings…** and paste your Client ID.

## Usage

### Building a set

1. **File → New** to start a fresh project.
2. Right-click in the **Section Panel** (left) → **Add Set List**, then **Add Section** to build the section structure.
3. Select a section, then use the context menu inside the **Track Table** (or **Ctrl+T**) → **Add track…** to add tracks manually, or import from a streaming service (see below).
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

### Fetching audio features

**Project → Fetch Audio Features…** authenticates with Spotify and downloads energy, danceability, and valence for all tracks that have a Spotify ID. The **energy overview bar at the bottom** visualises the set's energy arc.

### Exporting to Rekordbox

**Project → Export to Rekordbox…** → choose a set list and an output `.xml` file.
In Rekordbox: **File → Import Playlist from XML** and point to the exported file.
Each section appears as a sub-folder; track colors and comments carry over.

## Keyboard shortcuts

| Key | Action |
|-----|--------|
| `Ctrl+N` | New project |
| `Ctrl+O` | Open project |
| `Ctrl+S` | Save project |
| `Ctrl+Shift+S` | Save As |
| `Delete` | Remove selected track from section |
| `Ctrl+Q` | Quit |

## Project file format

`.setmgr` files are JSON with a version envelope:

```json
{ "version": "1", "project": { … } }
```

Fields are versioned; opening files from older releases is backwards-compatible.

## Distribution (standalone binary)

Install dev/dist dependencies and build:

```bash
make dev      # uv sync --all-extras
make dist     # uv run pyinstaller set_manager.spec --noconfirm
# Output: dist/SetManager.app  (macOS)  /  dist/SetManager/  (all platforms)
```

Or without Make:

```bash
uv sync --all-extras
uv run pyinstaller set_manager.spec --noconfirm
```

### macOS code-signing (optional)

After `make dist`, sign the bundle with your Apple Developer ID before distributing:

```bash
# Replace <TEAM_ID> with your 10-character Apple Developer Team ID.
codesign --deep --force --verify --verbose \
  --sign "Developer ID Application: Your Name (<TEAM_ID>)" \
  --entitlements entitlements.plist \
  dist/SetManager.app
```

For Mac App Store / Notarization, additionally run `xcrun altool --notarize-app` or
use `xcrun notarytool submit` (Xcode 14+).  Full notarization setup is outside the
scope of this project; see [Apple's documentation](https://developer.apple.com/documentation/security/notarizing_macos_software_before_distribution).

## Architecture

Model-View-Service:

| Layer | Location | Notes |
|-------|----------|-------|
| Models | `src/set_manager/models/` | Pydantic v2, no Qt dependency |
| Services | `src/set_manager/services/` | Streaming APIs, Rekordbox XML/DB, matching |
| GUI | `src/set_manager/gui/` | PySide6; Qt item models bridge domain → views |
| Utils | `src/set_manager/utils/` | Config, constants, dark theme |

## License

GPL-3.0-or-later
