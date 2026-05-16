# Connecting Streaming Services

Rekordbox Set List Manager can import playlists from **Spotify** and **Tidal**.  This page covers the one-time setup required for each service.

---

## Spotify

Spotify requires you to register a free Developer App and supply a Client ID.

### Step 1 — Create a Spotify Developer App

1. Go to [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard) and log in with your Spotify account.
2. Click **Create App**.
3. Fill in any name and description (e.g. "Rekordbox Set List Manager").
4. In the **Redirect URIs** field enter exactly:
   ```
   http://127.0.0.1:8888/callback
   ```
5. Save the app.
6. On the app's settings page, copy the **Client ID** (a 32-character hex string).

### Step 2 — Enter the Client ID in Rekordbox Set List Manager

1. Open **Project → Service Settings…**
2. Paste the Client ID into the **Spotify → Client ID** field.
3. Click **OK**.

### Step 3 — Authenticate

1. Open **Project → Import from Streaming Service…**
2. Select the **Spotify** tab.
3. Click **Connect**.  A browser window opens with the Spotify login page.
4. Log in and click **Agree** to grant access.
5. The browser redirects to `http://127.0.0.1:8888/callback` — Rekordbox Set List Manager catches the response automatically.

The access token is cached locally and reused for subsequent sessions.  You will only need to log in again if the token expires (typically after several months of inactivity).

### Spotify permissions requested

| Scope | Reason |
|---|---|
| `playlist-read-private` | Read your private playlists |
| `playlist-read-collaborative` | Read collaborative playlists you follow |
| `playlist-modify-public` | Push track order back to a public playlist (optional feature) |
| `playlist-modify-private` | Push track order back to a private playlist (optional feature) |

---

## Tidal

Tidal uses a **device-code flow** — no credentials or Developer App required.

### Step 1 — Authenticate

1. Open **Project → Import from Streaming Service…**
2. Select the **Tidal** tab.
3. Click **Connect to Tidal**.
4. A URL is printed to the terminal (and shown in the dialog).  Open it in a browser and log in with your Tidal account.
5. Approve access when prompted.

### Step 2 — Done

The session is cached to disk.  On future launches, Rekordbox Set List Manager reuses the cached session — the browser flow is only needed once (or if you clear the session manually).

### Clearing the cached Tidal session

Open **Project → Service Settings…** and click **Clear Cached Tidal Session**.  The next time you connect, the device-code flow runs again.

---

## Importing a playlist

Once authenticated (either service):

1. Open **Project → Import from Streaming Service…**
2. Select the service tab.
3. Choose a playlist from the dropdown.
4. (Optional) Select a **target section** — the import appends tracks to that section.  If no section is selected, a new section is created automatically.
5. Click **Import**.

The import dialog fetches all tracks, runs the matching engine against your Rekordbox collection, and adds the results to the selected section.

### What happens to unmatched tracks

Tracks that cannot be matched to a local Rekordbox file are still added to the section with their streaming metadata (artist, title, ISRC, duration).  You can later:

- Run **Project → Re-match with Rekordbox XML…** or **Re-match with Rekordbox DB** to try again after updating your collection.
- Use **Fix Match** on individual tracks.
- Leave them unmatched — they appear in the Rekordbox export without a local file reference.
