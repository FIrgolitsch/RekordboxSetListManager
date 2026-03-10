# Set Manager - Development Roadmap

Living tracker for development progress. Each phase builds on the previous one.
Point future coding sessions at this file to pick up where you left off.

---

## Phase 0: Project Scaffolding
> Foundation: pyenv, project config, empty GUI window

- [x] Create pyenv virtualenv (Python 3.14.2)
- [x] `.python-version`
- [x] `pyproject.toml` with all dependencies and tool config
- [x] `.gitignore`
- [x] `LICENSE` (MIT)
- [x] `README.md`
- [x] This `ROADMAP.md`
- [x] Package structure: `src/set_manager/` with `__init__.py`, `__main__.py`, `app.py`
- [x] Stub directories: `models/`, `services/`, `gui/`, `utils/`, `tests/`
- [x] `tests/conftest.py`
- [x] Install deps, verify `python -m set_manager` launches a window
- [x] Verify `pytest` and `ruff check` pass

**Done when**: `python -m set_manager` opens a Qt window, `pytest` exits 0.

---

## Phase 1: Domain Models + Project Persistence
> Core data structures and save/load

- [x] `models/enums.py` - SectionType, RekordboxColor, MatchStatus, TrackSource
- [x] `models/track.py` - Track model (title, artist, BPM, key, ISRC, match status, etc.)
- [x] `models/section.py` - Section model (name, type, color, ordered track IDs)
- [x] `models/set_list.py` - SetList model (name, date, venue, sections)
- [x] `models/project.py` - Project model (top-level container, section→color mapping)
- [x] `utils/constants.py` - Rekordbox color hex values, section presets
- [x] `services/project_io.py` - Save/load `.setmgr` JSON files
- [x] Unit tests for all models (creation, validation, serialization round-trip)
- [x] Unit tests for project_io (save → load → assert equality)

**Done when**: Can create a Project programmatically, save to `.setmgr`, reload, and assert equality.

---

## Phase 2: Rekordbox XML Export
> Generate valid XML that Rekordbox 7.2 can import

- [x] `services/rekordbox_xml.py` - RekordboxXmlService
- [x] `export_set()` - Generate XML with folder-per-section structure
- [x] Apply section colors to track `Colour` attribute
- [x] Write section name into track `Comments` field
- [x] Preserve BPM, key, duration, and other metadata in XML
- [x] `import_collection()` - Read existing Rekordbox XML exports
- [x] Integration tests with sample XML files (`tests/fixtures/rekordbox_collection.xml`)
- [ ] Verify XML color values empirically against Rekordbox 7.2 export
- [ ] Manual test: import generated XML into Rekordbox 7.2

**Done when**: Generated XML imports into Rekordbox 7.2 with correct folder structure and colors.

---

## Phase 3: Core GUI - Track Table & Set Structure
> Working desktop interface for manual set building

- [x] `gui/main_window.py` - Three-panel layout (set tree | track table | details)
- [x] `gui/models/track_table_model.py` - QAbstractTableModel for tracks
- [x] `gui/widgets/section_panel.py` - Tree view: SetList > Section hierarchy (QTreeWidget)
- [x] `gui/widgets/track_table.py` - Track table with columns: #, Title, Artist, BPM, Key, Duration, Status
- [x] `gui/widgets/add_track_dialog.py` - Form to add tracks manually
- [x] Add/remove tracks manually, create/rename/delete sections
- [x] Drag-and-drop reordering of tracks within the table
- [x] Section color display in track rows (background tint)
- [x] File menu: New, Open, Save, Save As (wired to ProjectIO)
- [x] `gui/widgets/export_dialog.py` - Export to Rekordbox XML, wired to RekordboxXmlService
- [x] Status bar: track count, total duration, unmatched count
- [x] Basic keyboard shortcuts (Ctrl+S, Ctrl+N, Ctrl+O, Delete)
- [x] `gui/widgets/theme_dialog.py` - Create, edit, rename, delete section name themes
- [x] Apply selected theme to a set list from the section panel (right-click context menu)
- [ ] Assign tracks to sections via drag-drop between sections (Phase 4+)

**Done when**: Can manually build a set with sections, reorder tracks, save/load, and export to Rekordbox XML.

---

## Phase 4: Spotify Integration
> Import playlists from Spotify, match to local files

- [x] `services/spotify_service.py` - SpotifyService
- [x] OAuth PKCE authentication (opens browser, caches token)
- [x] `get_playlists()` - List user's playlists
- [x] `get_playlist_tracks()` - Fetch tracks with ISRCs
- [x] `services/track_matcher.py` - Multi-strategy matching engine
- [x] ISRC exact match (highest priority)
- [x] Artist + title exact match (case-insensitive)
- [x] Artist + title fuzzy match (thefuzz, threshold ≥ 85%)
- [x] Filename-based match (parse artist - title from filename)
- [x] `gui/widgets/import_dialog.py` - Spotify tab with playlist browser
- [x] `gui/widgets/match_dialog.py` - Review matches, manual resolution for unmatched
- [x] `gui/widgets/settings_dialog.py` - Spotify Client ID configuration
- [x] Wire up: authenticate → browse → import → match → add to set
- [x] Tests with mocked Spotify responses

**Done when**: Can authenticate with Spotify, import a playlist, see match results, and add matched tracks to a set.

---

## Phase 5: Rekordbox Collection Import
> Read local Rekordbox library for matching

- [x] `services/rekordbox_db.py` - RekordboxDbService (read-only)
- [x] Auto-detect Rekordbox DB location via pyrekordbox
- [x] `get_collection()` - Load all tracks as Track objects
- [x] `find_track_by_isrc()` and `find_track_by_path()`
- [x] Enhance TrackMatcher to use Rekordbox collection as match source
- [x] Collection browser in ImportDialog — "Auto-detect Rekordbox DB" button
- [x] Display BPM and key from Rekordbox analysis data
- [ ] Handle large collections efficiently (lazy loading, indexing)

**Done when**: Track matching uses the actual Rekordbox collection. BPM/key from Rekordbox analysis visible in track table.

---

## Phase 6: Tidal Integration
> Import playlists from Tidal (mirrors Spotify flow)

- [x] `services/tidal_service.py` - TidalService
- [x] Device-code authentication (opens browser link via console, caches session to `platformdirs` user_cache_dir)
- [x] `get_playlists()` and `get_playlist_tracks()` with ISRCs
- [x] Tidal tab in ImportDialog (shared track table + Rekordbox + Match + Import)
- [x] Tidal session management in SettingsDialog (info + "Clear Cached Session" button)
- [x] Wire up same matching pipeline as Spotify (TrackMatcher.match() is service-agnostic)
- [x] 14 new tests (mocked tidalapi); 167 total, all passing

**Done when**: Full Tidal playlist import with matching, same UX as Spotify flow.

---

## Phase 7: Audio Features & Set Flow
> Energy/danceability display for planning set flow

- [x] `services/audio_features.py` - Fetch Spotify audio features, cache results
- [x] Add energy, danceability, valence columns to track table
- [x] `gui/widgets/set_overview.py` - Horizontal energy timeline visualization
- [x] Color-coded energy indicators in track rows
- [x] Cache audio features in project file to avoid repeated API calls
- [x] Batch-fetch features for efficiency

**Done when**: Energy curve visible across the set, helping plan flow and transitions.

---

## Phase 8: Polish & Advanced Features
> Production quality and UX refinements

- [x] Transition notes between tracks (editable panel below track table)
- [x] Undo/redo — memento approach: 50-step JSON snapshot stack, Ctrl+Z / Ctrl+Y
- [x] Column sorting and filtering in track table
- [x] Dark theme QSS stylesheet
- [x] Application icon
- [x] `utils/config.py` - Persistent app config via platformdirs
- [x] Error handling: graceful degradation when services unavailable
- [x] Packaging (PyInstaller spec `set_manager.spec`; `pip install ".[dist]"`)
- [x] User documentation

**Done when**: App feels polished, handles errors gracefully, and can be distributed as a standalone binary.

---

## Notes

- **Rekordbox XML** is the primary export path (officially supported, safe). Direct DB writes are avoided.
- **ISRC codes** are the most reliable cross-service track identifier.
- **pyrekordbox** is tested up to RB 7.0.9 — verify compatibility with 7.2 in Phase 2.
- Always back up the Rekordbox collection before testing XML imports.
