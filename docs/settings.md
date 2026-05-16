# Settings

Open **Project → Service Settings…** to manage credentials and preferences.

---

## Spotify

| Field | Description |
|---|---|
| **Client ID** | The Client ID from your Spotify Developer Dashboard app.  See [Connecting Streaming Services → Spotify](connecting-services.md#spotify) for setup instructions. |

The Redirect URI is fixed at `http://127.0.0.1:8888/callback`.  Make sure this exact value is added to your Spotify Developer App's **Redirect URIs** list.

---

## Tidal

No credentials are required for Tidal.  Authentication uses a device-code flow initiated from the Import dialog.

| Button | Description |
|---|---|
| **Clear Cached Tidal Session** | Delete the stored session file.  The next connection will require a new browser login. |

---

## Usage data (telemetry)

| Setting | Description |
|---|---|
| **Send anonymous usage data (opt-in)** | When checked, anonymous events are written to a local log file. |

Telemetry is **opt-in** and **local-only** — no data is sent to any server.  The log file is stored at:

| Platform | Log location |
|---|---|
| macOS | `~/Library/Caches/rekordbox_set_list_manager/events.jsonl` |
| Linux | `~/.cache/rekordbox_set_list_manager/events.jsonl` |
| Windows | `%LOCALAPPDATA%\rekordbox_set_list_manager\events.jsonl` |

### What is logged

Anonymous counts and event types only:

- App start / stop
- Project save / open (file path is **not** recorded)
- Import counts (how many tracks were imported and matched, by strategy)
- Error types (exception class names, no stack traces or user data)

### What is never logged

- File paths
- Track names, artist names, or any track metadata
- Spotify or Tidal credentials
- Any personally identifiable information

To stop all logging, uncheck the box and click **OK**.  Existing log data can be deleted by removing the `events.jsonl` file.

---

## Configuration storage

All settings (Spotify Client ID, telemetry preference) are stored in the system user-config directory:

| Platform | Config location |
|---|---|
| macOS | `~/Library/Application Support/rekordbox_set_list_manager/` |
| Linux | `~/.config/rekordbox_set_list_manager/` |
| Windows | `%APPDATA%\rekordbox_set_list_manager\` |

The config file is plain JSON and can be edited by hand if needed.
