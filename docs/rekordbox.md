# Working with Rekordbox

Rekordbox Set List Manager integrates with your local Rekordbox library in two ways:

1. **Collection loading** — reads your library so the matching engine can link streaming tracks to local files.
2. **XML export** — writes a `DJ_PLAYLISTS` XML file that Rekordbox can import as a folder of playlists.

---

## Loading your Rekordbox collection

The matching engine needs a copy of your Rekordbox library to work.  There are two sources:

### Option A — Auto-detect the local database (recommended)

On macOS, Rekordbox stores its database at:

```
~/Library/Pioneer/rekordbox/master.db
```

Rekordbox Set List Manager detects this file automatically.  No configuration is needed.  The database is opened read-only.

> **Note:** Close Rekordbox before running Rekordbox Set List Manager if you are on the same machine and want the freshest data.  SQLite allows multiple readers, but a locked write transaction in Rekordbox can delay reads.

### Option B — Import a Rekordbox XML export

1. In Rekordbox, go to **File → Export Playlist in XML Format** (or export the entire library).
2. In Rekordbox Set List Manager, the Import dialog has a **Collection** section — click **Browse** and select the exported `.xml` file.

XML export is useful when:
- You are on a different machine (e.g. a laptop without Rekordbox installed).
- You want a snapshot of the collection at a specific point in time.

---

## Matching

When you import a streaming playlist, the matching engine attempts to link each streaming track to a local file using four strategies in order:

| Priority | Strategy | Description |
|---|---|---|
| 1 | **ISRC** | Exact match on International Standard Recording Code — the most reliable method |
| 2 | **Exact** | Case-insensitive exact match on artist + title |
| 3 | **Fuzzy** | Similarity match using token-sort ratio (threshold: 85 / 100) |
| 4 | **Filename** | Parse the local file's stem as "Artist - Title" |

If none of the strategies succeeds, the track is marked `unmatched`.

The strategy used is recorded and visible in the **Match** column and in the **Match Info** panel.

---

## Re-matching

If you update your Rekordbox library after importing a playlist, you can re-run the matching engine without re-importing the playlist:

- **Project → Re-match with Rekordbox XML…** — select a new Rekordbox XML export.
- **Project → Re-match with Rekordbox DB** — re-read the auto-detected local database.

Re-matching updates all tracks in the project, replacing old results only if a better match is found.  Manually matched tracks are preserved.

---

## Fixing a match manually

If the automatic match assigned the wrong local file:

1. Right-click the track → **Fix Match…**
2. In the Fix Match dialog, search for the correct track in your Rekordbox collection.
3. Select the correct row and click **Accept**.

The match status changes to `manually_matched`.  The fix is preserved through re-matching operations.

---

## Exporting to Rekordbox XML

**Project → Export to Rekordbox…** generates a `DJ_PLAYLISTS` XML file.

The export structure:

```
DJ_PLAYLISTS
└── COLLECTION      (all unique tracks referenced by the set)
└── PLAYLISTS
    └── <Project name>  (folder)
        ├── Opener      (playlist — one per section)
        ├── Build
        ├── Peak
        └── Closing
```

Each playlist entry in the XML carries:
- A reference to the local file in the `COLLECTION`
- The section's Rekordbox colour in the `Colour` attribute
- The section name in the `Comments` attribute

### Importing the XML into Rekordbox

1. In Rekordbox, go to **File → Import Library XML** (or drag the file into the Library panel).
2. Navigate to the folder named after your project.
3. Drag sections into your USB stick or Hot Cue playlists as needed.

> **Rekordbox 7 note:** Rekordbox 7.x reads the `Colour` field from the XML and maps it to the correct colour chip.  The nine colours exported by Rekordbox Set List Manager are verified against Rekordbox 7.2.14.  Other colour values are not supported.
