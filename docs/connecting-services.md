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

Tidal uses a **PKCE login flow** — no credentials to configure.  The app authenticates with its built-in client pair, which is the same client identity Tidal's own app uses, so lossless audio works when your subscription includes it.

### Step 1 — Authenticate

Either of these works — both run the same flow and share the same cached session:

- Open **Project → Service Settings…** and click **Connect to Tidal…** in the Tidal section, or
- Open **Project → Import from Streaming Service…**, select the **Tidal** tab, and click **Connect**.

Then:

1. A login link appears in the dialog.  Open it in a browser and log in with your Tidal account.
2. After logging in, the browser is redirected to an **"Oops"** page — this is expected.  Copy that page's full address (it starts with `https://tidal.com/android/login/auth?code=…`).
3. Paste it into the **Complete Login** field in the dialog.

### Step 2 — Done

The session is cached to disk.  On future launches, Rekordbox Set List Manager reuses the cached session — the browser flow is only needed once (or if you clear the session manually).

The cached session records which client credentials minted it.  If you configure a different `client_id`/`client_secret`, the app detects the change, discards the stale session, and re-runs the device-code flow automatically — a session minted under a different client can never silently keep serving at that client's quality cap.

### Optional — your own client credentials

The PKCE flow uses the app's built-in client pair and needs no configuration.  Advanced users can override the non-PKCE client pair used for session bookkeeping via **either** of two places — the environment variables win when both are set:

- The environment variables `SET_MANAGER_TIDAL_CLIENT_ID` and `SET_MANAGER_TIDAL_CLIENT_SECRET`, or
- The `tidal_client_id` and `tidal_client_secret` keys in `config.json` inside the app's user config directory (e.g. `~/Library/Application Support/rekordbox_set_list_manager/` on macOS).

### Clearing the cached Tidal session

Open **Project → Service Settings…** and click **Clear Cached Tidal Session**.  The next time you connect, the device-code flow runs again.

---

## Importing a playlist

Once authenticated (either service):

1. Open **Project → Import from Streaming Service…**
2. Select the service tab.
3. Choose a playlist from the list.
4. Click **Match** to run the matching engine against your Rekordbox collection, then **Import**.
5. Tracks are appended to the section currently selected in the main window.  If no section is selected, a new **Imported** section is created automatically.

### What happens to unmatched tracks

Tracks that cannot be matched to a local Rekordbox file are still added to the section with their streaming metadata (artist, title, ISRC, duration).  You can later:

- Run **Project → Re-match with Rekordbox XML…** or **Re-match with Rekordbox DB** to try again after updating your collection.
- Use **Fix Match** on individual tracks.
- Leave them unmatched — they appear in the Rekordbox export without a local file reference.
