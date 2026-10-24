# Setting up Crate

This takes about an hour, spread over a few sittings. You'll set up five things, in this order:

1. [Ask Spotify for your listening history](#1-ask-spotify-for-your-listening-history) (do this first: it takes days to arrive)
2. [Last.fm: scrobbling and an API key](#2-lastfm-scrobbling-and-an-api-key)
3. [A Spotify developer app](#3-a-spotify-developer-app)
4. [Notion: a page and an integration](#4-notion-a-page-and-an-integration)
5. [Your own private copy of Crate on GitHub](#5-your-own-private-copy-of-crate-on-github)

Then [import your history](#6-import-your-history) and [install the app](#7-install-the-app).

> **Tip:** you can paste [CLAUDE_SETUP_PROMPT.md](CLAUDE_SETUP_PROMPT.md) into Claude and have it walk you through all of this.

**Keep your keys safe.** You'll collect four keys below. They go in exactly two places: GitHub's *secrets* (step 5) and the Crate app on your phone (step 7). Never paste them into a file in the repository, an issue, or a chat.

---

## 1. Ask Spotify for your listening history

Optional, but it's what makes Crate good: Spotify's *Extended streaming history* goes back to the day you joined. Without it, Crate starts from your Last.fm history.

1. Go to [spotify.com/account/privacy](https://www.spotify.com/account/privacy/) and log in.
2. Under **Download your data**, tick **Extended streaming history**. You can untick the rest.
3. Click **Request data** and confirm in the email Spotify sends you.

Spotify says it can take up to 30 days; it's often a few days. You'll get an email with a ZIP file. Carry on with the next steps while you wait.

## 2. Last.fm: scrobbling and an API key

Last.fm keeps track of what you play from now on ("scrobbling"), and gives Crate tags (for genres), similar artists and bios.

1. Make a free account at [last.fm](https://www.last.fm/join) if you don't have one.
2. Connect Spotify: [last.fm/settings/applications](https://www.last.fm/settings/applications) → **Spotify scrobbling** → **Connect**. Play a song in Spotify and check it shows up on your Last.fm profile.
3. Get an API key: [last.fm/api/account/create](https://www.last.fm/api/account/create). Fill in your email and an application name (for example *Crate*); leave the rest empty. Copy the **API key**. (The *shared secret* isn't needed.)

Write down your **Last.fm username** too.

## 3. A Spotify developer app

Crate uses Spotify's catalogue (artist photos, album covers, release dates, links) and, if you have Premium, plays music through the Spotify app on your phone.

1. Go to [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard), log in with your normal Spotify account and accept the terms.
2. Click **Create app** and fill in:
   - **App name**: Crate. **App description**: My music library.
   - **Redirect URIs**: `crate-music://spotify` (click **Add**).
   - **Which API/SDKs are you planning to use?** Tick **Web API** and **Android**.
   - Accept the terms and **Save**.
3. On the app's page, click **Settings**. Copy the **Client ID**, then click **View client secret** and copy the **Client secret**.
4. Still in Settings, click **Edit** and find **Android packages**. Fill in both boxes, then click **Add** under them (an entry only counts once it's listed with a *Remove* button):
   - Package name: `io.github.pvandenboer_alt.crate`
   - Package SHA1 fingerprint: `AD:D2:9D:A5:5D:3A:D4:77:0E:1F:7F:F3:A7:DC:83:71:0A:0B:E9:72`

   These belong to the ready-made app you'll install in step 7. (If you build the app yourself, use the fingerprint your build prints in its run summary instead.)
5. Scroll down and click **Save**.
6. Open the **User Management** tab and add yourself: your name and the email address of your Spotify account. Development-mode apps only work for the people listed here (up to five).

## 4. Notion: a page and an integration

Your library lives in Notion. The setup step creates five databases (Artists, Albums, New Releases, Blocklist, Crate log) and a *Crate report* page for you.

1. In Notion, make a new page called **Music Library** (any name works). Everything goes inside it.
2. Go to [notion.so/profile/integrations](https://www.notion.so/profile/integrations) → **New integration**:
   - Name: Crate. Workspace: yours. Type: **Internal**. Save.
   - Under **Capabilities**, tick **Read content**, **Update content** and **Insert content**. For user capabilities, choose **No user information**. Save.
   - Copy the **Internal Integration Secret** (it starts with `ntn_`).
3. Back on your Music Library page: **•••** (top right) → **Connections** → find **Crate** and confirm. The integration can now see that page and everything in it, and nothing else in your Notion.

## 5. Your own private copy of Crate on GitHub

The sync runs in your own copy of this repository, on GitHub Actions, for free.

1. On the Crate repository page, click **Use this template** → **Create a new repository**.
   - Owner: you. Name: for example `crate`.
   - Choose **Private**. This matters: your copy will hold your listening history. The workflows refuse to run in a public repository.
   - Click **Create repository**.
2. Add your keys as secrets: in your new repository, **Settings** → **Secrets and variables** → **Actions** → **New repository secret**, once for each:

   | Name | Value |
   |---|---|
   | `NOTION_TOKEN` | The Notion integration secret (`ntn_…`) |
   | `LASTFM_API_KEY` | The Last.fm API key |
   | `SPOTIFY_CLIENT_ID` | The Spotify Client ID |
   | `SPOTIFY_CLIENT_SECRET` | The Spotify Client secret |

3. Fill in your settings: open `sync/config.yml`, click the pencil (**Edit**), and set the three lines marked `YOURS`:
   - `lastfm_user`: your Last.fm username.
   - `timezone`: where you live, for example `Europe/London`, `America/New_York` or `Australia/Sydney` ([list](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones)).
   - `market`: your Spotify country as two letters, for example `GB`, `US`, `NL`.

   Click **Commit changes**.
4. Open the **Actions** tab. If GitHub asks, click **I understand my workflows, go ahead and enable them**.
5. Create the Notion databases: **Actions** → **Set up Notion** → **Run workflow** → **Run workflow**. After a minute it shows a green tick, and the run's summary lists what it created. Your Music Library page in Notion now holds the five databases and the report page.
   - If it says your integration can see several pages, run it again and paste the link of your Music Library page in the **page** box.

## 6. Import your history

### With the Spotify export (recommended)

1. Unzip Spotify's ZIP. You'll find a folder *Spotify Extended Streaming History* with files named `Streaming_History_Audio_….json`. (Ignore the `Video` files.)
2. **Strip the private bits first.** These files also hold your IP address, devices and podcasts. With Python 3 installed, from a copy of this repository:

   ```bash
   python sync/slim_export.py "path/to/Spotify Extended Streaming History" slimmed
   ```

   This writes smaller copies with only what Crate needs into a folder called `slimmed`. (Claude can do this for you; see the prompt.)
3. Upload them: in your repository, open the `data` folder → **Add file** → **Upload files**, drag in the `Streaming_History_Audio_….json` files, and **Commit changes**.
4. **Dry run**: **Actions** → **Import Spotify history** → **Run workflow**, with **Dry run** ticked. When it's done, open the run and read the **Summary**: totals, your top artists and albums, how many pass the thresholds, likely duplicates.
5. Adjust if you like (see [Tuning](#tuning)), then run it again with **Dry run** unticked. The real import can take an hour or more: it looks up every artist on Last.fm, Spotify and Deezer. Re-running is safe; nothing gets duplicated.

### Without the export

Run the same **Import Spotify history** workflow with an empty `data` folder (dry run first, then for real): it imports your whole Last.fm history instead. If the export arrives later, upload it and run the import again; plays that are in both are counted once.

### After that

- **Weekly sync** runs every Monday at 06:00 UTC (change the `cron` line in `.github/workflows/sync.yml` to move it). It adds new scrobbles, artists and albums, refreshes genres and levels, and checks for new releases.
- **Backfill** runs every six hours and fills in what earlier runs couldn't, mostly Spotify IDs (Spotify limits how much a development-mode app may ask, so a big library takes a few days).
- GitHub emails you when a run fails. The most common reason is a weekly sync that found no scrobbles for 7 days: reconnect Spotify in Last.fm's settings.

## 7. Install the app

1. On your Android phone, download **[Crate.apk](https://github.com/pvandenboer-alt/crate/releases/download/crate/Crate.apk)** and open it. Android asks you to allow installing apps from your browser; allow it for this install.
2. Open Crate and paste your Notion integration secret (the same `ntn_…` key). Crate finds its databases and loads your library. Big libraries take a minute the first time; after that it works offline and refreshes in seconds.
3. **To play music from Crate** (Spotify Premium): **Settings** → **Spotify** → paste your Spotify **Client ID** (not the secret) → **Save and connect**. Open the Spotify app once first so it's awake. Spotify asks you once to allow Crate to control playback; if the Spotify app can't show that, Crate opens Spotify's approval page in your browser.

Updates: download the new Crate.apk from the same link; it installs over the old one and keeps your settings.

---

## Tuning

All of these are optional.

- **Thresholds** (`sync/config.yml`): how many plays an artist or album needs to enter your library. Dry-run the import to see how changes move the numbers.
- **Genres** (`sync/genre_map.yml`): which Last.fm tags map to which genre. You can also set genres per artist in Crate (artist page → •••); your choices stick.
- **Blocklist**: block artists, albums, songs or genres from Crate (••• on any page). Anything the sync should always skip (kids' music, sleep sounds) can also go in `sync/blocklist.yml`.
- **Aliases** (Notion, Artists → *Aliases*): other names of the same artist, separated by `;`, for example `Prince; The Artist Formerly Known as Prince`. Plays roll up to the artist on the next sync.
- **Not interested**: set an artist's or album's *Status* to *Excluded* in Notion and the sync leaves it alone for good.
- **New releases**: Crate checks the top 35% of your artists (Inferno, Blaze and Flame). Tick *Watch releases* on any other artist to add them.

## Building the app yourself

Only needed if you change the app. Every push to `app/` runs **Build Crate**, which publishes `Crate.apk` on your copy's own *crate* release. Its run summary shows the SHA1 fingerprint to put in your Spotify dashboard (step 3.4).

To make updates install over each other, sign every build with the same key: create a keystore (`keytool -genkeypair -v -storetype pkcs12 -keystore crate.keystore -storepass android -alias androiddebugkey -keypass android -keyalg RSA -keysize 2048 -validity 10000 -dname "CN=Crate"`), keep it safe, and add it base64-encoded as the secret `ANDROID_KEYSTORE_BASE64` (`base64 -i crate.keystore | pbcopy` on a Mac). Without that secret, each build uses a new throwaway key.

## Troubleshooting

| You see | What to do |
|---|---|
| *This repository is public* | Make your copy private: Settings → General → Danger Zone → Change visibility. |
| *Crate isn't set up in this repository* | Add the `NOTION_TOKEN` secret (step 5.2). |
| *No Notion database IDs yet* | Run **Set up Notion** (step 5.5). |
| Set up Notion: *can't see any page* | Add the integration to your Music Library page (step 4.3). |
| Crate: *can't find Artists, Albums…* | Run **Set up Notion**, and check the integration is connected to the page that holds the databases. |
| Crate: *more than one … database* | Remove the integration from the copy Crate shouldn't use (••• → Connections on that database). |
| Weekly sync fails: no recent scrobbles | Reconnect Spotify scrobbling in Last.fm (step 2.2). |
| Summary: *Spotify rate limit* | Nothing to do: Crate waits and the backfill carries on later. |
| Play opens Spotify instead of playing | Check Settings → Spotify in Crate. The grey line shows Spotify's reason; usually the Android package or fingerprint in the Spotify dashboard (step 3.4), or your account missing from User Management (step 3.6). |
