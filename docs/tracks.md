# Tracks

---

## Adding tracks

### Import from a streaming service

The most common workflow is to import an entire Spotify or Tidal playlist at once.  See [Connecting Streaming Services](connecting-services.md) and [Working with Rekordbox](rekordbox.md) for the full import flow.

### Add a track manually

1. Select a section.
2. Click **Add Track** in the section header, or press `Ctrl+T`.
3. Fill in the track details in the Add Track dialog:
   - **Artist** (required)
   - **Title** (required)
   - **BPM** (optional)
   - **Key** (optional)
4. Click **OK**.

Manually added tracks have source `manual` and match status `unmatched` until matched against the Rekordbox collection.

---

## Removing tracks

Select one or more track rows and press `Delete`, or right-click → **Remove Track**.  Removing is undoable.

---

## Reordering tracks

Drag a row to a new position within the same section, or drag it into a different section's track table.

---

## Filtering and sorting

A **search box** at the top of each section filters tracks in real time by artist or title — useful in large sections.

Click any **column header** to sort by that column; click again to reverse the order.  Sorting is in-memory and does not reorder the saved section.

---

## Track match status

After importing from a streaming service, each track is matched against your Rekordbox library.  The match status is displayed in the track table and in the Match Info panel.

| Status | Meaning |
|---|---|
| `matched` | A local Rekordbox file was found automatically |
| `manually_matched` | You manually assigned a local file via Fix Match |
| `unmatched` | No local file could be found |
| `conflicted` | Multiple potential matches were found; manual review required |

Unmatched tracks are still included in the project and in exports; they just won't carry local metadata (BPM, key, file path).

---

## Match strategies

The matching engine tries four strategies in order:

1. **ISRC** — exact match on the international recording code.  Most reliable; requires both the streaming track and the local file to have an ISRC.
2. **Exact** — case-insensitive exact match on artist + title.
3. **Fuzzy** — similarity match on artist + title using token-sort ratio; threshold is 85 out of 100.
4. **Filename** — parses the local file's stem as "Artist - Title" and compares.

The strategy used is shown in the **Match** column and in the Match Info panel.

---

## Fixing a bad match

If the automatic match is incorrect:

1. Right-click the track → **Fix Match…**
2. In the Fix Match dialog, type a search term to find the correct local track.
3. Select the correct row and click **Accept**.

The track's match status changes to `manually_matched` and the strategy is recorded as `manual`.

---

## Track source

Each track has a **source** field indicating where its metadata came from:

| Source | Description |
|---|---|
| `spotify` | Imported from a Spotify playlist |
| `tidal` | Imported from a Tidal playlist |
| `rekordbox` | Added directly from the Rekordbox collection |
| `manual` | Added manually via the Add Track dialog |
