# Troubleshooting

## Spotify

### "Spotify Client ID not configured"

Open **Project → Service Settings…** and enter your Client ID.  See [Connecting Streaming Services → Spotify](connecting-services.md#spotify) for instructions on creating a Developer App.

### The browser opens but auth never completes

Make sure the Redirect URI in your Spotify Developer App settings is **exactly**:

```
http://127.0.0.1:8888/callback
```

No trailing slash, no https, no different port.

### "Authentication failed"

Try clearing the cached token:

1. Delete `~/Library/Caches/rekordbox_set_list_manager/.spotify_cache` (macOS) or the equivalent on your platform.
2. Restart Rekordbox Set List Manager and try connecting again.

---

## Tidal

### The device-code URL is not shown in the dialog

The URL is shown in the dialog as a clickable link while the login waits.  If it does not appear, click **Connect** again — the device-code link may have expired.  (Only non-GUI launches print the URL to stdout.)

### "Authentication was not completed"

The device-code login has a time limit (~5 minutes).  If it expires, click **Connect to Tidal** again.

### Session expired on relaunch

Tidal sessions last several months.  If yours has expired, click **Connect to Tidal** again to authenticate.  You can also clear the session manually from **Project → Service Settings…** → **Clear Cached Tidal Session**.

---

## Importing playlists

### Some tracks were skipped when the playlist loaded

When a streaming playlist loads, entries that cannot be imported — podcast
episodes, empty playlist slots, tracks with no title, or unreadable entries —
are skipped.  The status line under the track list shows how many were
skipped, and the **Skipped details…** button next to it opens a report
listing each skipped entry with the reason it was dropped.

The same report also lists duplicate ISRCs found in your Rekordbox collection
after running **Match with Collection**.  Those tracks are still matched —
deterministically, to the lowest Rekordbox ID — and the report shows which
ISRCs were ambiguous so you can clean up the duplicates in Rekordbox.

---

## Rekordbox collection

### "master.db not found" / auto-detection fails

Rekordbox Set List Manager looks for the database at `~/Library/Pioneer/rekordbox/master.db`.  If Rekordbox is installed in a non-standard location, the database may not be at that path.

**Workaround:** Export your library from Rekordbox as XML (**File → Export Playlist in XML Format**) and load the XML via the Import dialog's **Browse** button instead.

### All tracks unmatched after import

Check the following:

1. The Rekordbox collection was loaded (the import dialog shows a track count > 0 in the Collection section).
2. Your streaming tracks have ISRCs — check the Match Info panel.  Without ISRCs, the engine falls back to fuzzy matching, which requires reasonable artist/title data.
3. The local files in your Rekordbox library have their metadata correctly filled in (artist + title).
4. Several collection tracks share the same ISRC — click **Skipped details…** after matching; duplicates resolve to the lowest Rekordbox ID, so merge or re-tag the extras in Rekordbox.

### Wrong track matched

Right-click the track → **Fix Match…** and manually select the correct local file.

---

## Projects

### "Unexpected file extension" on open

Rekordbox Set List Manager expects `.setmgr` files.  If the file was renamed, change the extension back to `.setmgr`.

### Autosave restore prompt on launch

Rekordbox Set List Manager found an autosave file newer than the saved project (usually from a previous crash).  Click **Restore** to recover your work, or **Discard** to ignore it and open the last manually saved version.

---

## Performance

### The app is slow with large collections

The Rekordbox database reader loads the full collection into memory on first use.  Libraries with 50,000+ tracks can take a few seconds to load.  Once loaded, the collection is kept in memory for the session.

### Import takes a long time

Fetching all tracks in a large playlist (1,000+ tracks) involves multiple API calls and may take up to a minute.  A progress bar is shown in the Import dialog.

---

## General

### The window is blank / does not render correctly

Rekordbox Set List Manager uses PySide6 (Qt 6).  On some Linux configurations, Wayland or GPU compositing can cause rendering issues.  Try launching with:

```bash
QT_QPA_PLATFORM=xcb uv run rslm
```

### Reporting a bug

Please open an issue on the project's GitHub repository.  Include:
- Your operating system and version
- The Rekordbox Set List Manager version (`rslm --version`)
- Steps to reproduce the problem
- Any error messages from the terminal
