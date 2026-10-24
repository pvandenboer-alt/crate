# Security

## Reporting a problem

Please report security issues privately, through GitHub: **Security** tab → **Report a vulnerability** on this repository. Don't open a public issue for them. You'll get an answer within a week.

## How Crate handles your keys and data

**Keys**

| Key | Where it lives | Used by |
|---|---|---|
| Notion integration secret | GitHub secret `NOTION_TOKEN` in your private copy; and in the Crate app's storage on your phone | The sync; the app |
| Last.fm API key | GitHub secret `LASTFM_API_KEY` | The sync |
| Spotify client ID and secret | GitHub secrets `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`; the client ID (not the secret) also in the app | The sync (catalogue data only, no login); the app (remote control of the Spotify app) |
| App signing key (optional) | GitHub secret `ANDROID_KEYSTORE_BASE64` | Your own app builds |

- Nothing personal is built into the app. The published `Crate.apk` is the same for everyone and contains no keys or IDs.
- The workflows pass keys to the sync as environment variables. They're never printed, logged or committed.
- Give the Notion integration access to your Music Library page only (••• → Connections). It can't see the rest of your workspace.

**Your listening history**

- The sync commits play counts and scrobble times to your copy (`state/`), and you upload your Spotify export to `data/`. That's why your copy must be private. Every workflow that writes to the repository checks first and stops if the repository is public.
- `sync/slim_export.py` strips your IP address, devices, country, podcasts and audiobooks from the export before you upload it.
- Crate sends artist and album names (never your history) to Spotify, Last.fm and Deezer to look up photos, covers, tags, bios and releases.
- The app's usage log (what it showed you, what you opened and played; IDs only) goes to the *Crate log* database in your own Notion, and nowhere else. There's no analytics or tracking.

**The app**

- It talks to `api.notion.com` with your key, and to the Spotify app on your phone (Spotify's App Remote SDK) if you add a client ID. It loads pictures from Spotify, Deezer and Last.fm image servers.
- Everything it shows from Notion is escaped before it's put on screen, and links to Spotify are only built from validated Spotify IDs.
- It's a release build (not debuggable). The Notion key is stored in the app's private storage, like any app's login. Anyone with your unlocked phone can use the app; use your phone's lock screen.
- Spotify's App Remote SDK is downloaded from Spotify's GitHub release at build time and checked against a known SHA-256 hash.

**The workflows**

- Third-party actions are pinned to exact commits.
- The Tests workflow, the only one that runs on pull requests, has read-only access and no secrets.
- Workflow inputs (like the page link for Set up Notion) reach scripts as environment variables, never pasted into shell commands.

## Supported versions

Only the latest version on the `main` branch and the latest `Crate.apk`.
