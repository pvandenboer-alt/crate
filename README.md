# Crate

**Rediscover the music you used to love.** Crate turns years of Spotify listening into a personal music library in Notion, keeps it up to date from Last.fm, and gives you an Android app (in the style of Apple Music) built around rediscovery instead of what you played last week.

Streaming apps push what you played recently. If you've listened for ten or fifteen years, most of your favourites are buried. Crate digs them up.

## What you get

- **A music library in Notion**: every artist and album you've really listened to, with all-time play counts per year and month, your top songs per artist, genres, photos, covers, short bios, similar artists and new releases. Notion is the database; you can browse, edit and fix it there.
- **The Crate app** (Android):
  - **Rediscover**: artists who were big for you once and have been quiet for a year, new picks every day. Plus Today's Pick, *This month, years ago*, On Repeat, Albums You Missed, Start to Finish and Albums to Revisit.
  - **Heat**: a five-step scale (Spark, Ember, Flame, Blaze, Inferno) on every artist, album and song, based on your own history, with a Heat tab for your all-time greats and heat filter chips on every page.
  - **Browse** by genre, year and month, with genre pages and year pages filtered by genre.
  - **New Releases**: albums and EPs from your favourite artists, grouped by genre, with track lists and the songs you already know from them.
  - **Play** through the Spotify app without leaving Crate, with a mini player and a full-screen player (seek, shuffle, repeat, Liked Songs). Optional, needs Spotify Premium.
  - **Crate Stats**: how many artists you rediscovered each month, and which features led you there.
  - Works offline, blocklist for anything you don't want to see, and no account, ads or tracking: it talks only to your Notion and Spotify.
- **Automatic upkeep**: a weekly sync (new scrobbles, new artists and albums, genres, levels, new releases) and a small six-hourly backfill, both on GitHub Actions in your own copy.

## How it works

```
Spotify data export ──┐
                      ├─► sync (Python, GitHub Actions in your private copy) ──► Notion databases ◄── Crate app
Last.fm scrobbles ────┘          + Spotify, Deezer and Last.fm catalogue data                 (on your phone)
```

- The **sync** runs in *your own private copy* of this repository, with your keys stored as GitHub secrets. It imports your Spotify history once, then adds Last.fm scrobbles every week.
- The **app** reads your Notion databases directly, with a Notion key you paste into it once. The key stays on your phone. The app has no server of its own.

## Get started

You need: an Android phone, and free accounts with GitHub, Notion, Last.fm and Spotify for Developers. Spotify Premium is only needed to play music from inside Crate.

**Follow [docs/SETUP.md](docs/SETUP.md).** It takes about an hour of clicking, plus a wait of a few days to a few weeks for Spotify to send your data export. You can start with Last.fm only and add the export later.

Prefer a guide? [docs/CLAUDE_SETUP_PROMPT.md](docs/CLAUDE_SETUP_PROMPT.md) is a prompt for Claude (or another AI assistant) that walks you through the setup, and helps you change Crate to suit you.

**Download the app:** [Crate.apk](https://github.com/pvandenboer-alt/crate/releases/download/crate/Crate.apk) (from the [crate release](https://github.com/pvandenboer-alt/crate/releases/tag/crate)). It's built by GitHub Actions from this repository, with no keys inside.

## Privacy and security

- **Your copy must be private.** The sync saves your listening history in your copy of the repository (`state/`, and the export in `data/`). The workflows refuse to run in a public repository.
- **No keys in the app.** The Notion key and the Spotify client ID are entered on your phone and stay there. The APK in this repository contains nothing personal and is the same for everyone.
- **Your keys stay in GitHub secrets**, used only by the workflows in your private copy. They're never printed or committed.
- **Where your data goes**: your Notion workspace (the library), your private GitHub repository (play counts), and the music APIs Crate looks things up in (Spotify, Last.fm, Deezer get artist and album names, never your history). The app's usage log goes only to your own Notion.
- **Strip your export first**: `sync/slim_export.py` removes your IP address, device and country, plus podcasts and audiobooks, before you upload the export.

More in [SECURITY.md](SECURITY.md).

## Good to know

- Android only, for now. The app is a [Capacitor](https://capacitorjs.com) app: plain HTML, CSS and JavaScript in `app/www`.
- Spotify's API limits apps in development mode. Crate spaces out its requests and stops at the first warning, so filling in Spotify IDs for a big library can take a few days of backfill runs. Photos and covers come from Deezer in the meantime.
- GitHub Actions minutes: the workflows use a few hundred minutes a month, within the 2,000 free minutes for private repositories.
- Crate isn't affiliated with Spotify, Notion, Last.fm or Deezer.

## Repository

| Path | What |
|---|---|
| `app/` | The Crate app (`www/` web code, `native/android/` the Spotify remote plugin) |
| `sync/` | The Python sync: `setup.py`, `importer.py`, `weekly.py`, `backfill.py`, settings in `config.yml`, `genre_map.yml`, `blocklist.yml` |
| `sync/tests/` | End-to-end tests against fakes (no network, no keys): `python -m pytest -q sync/tests` |
| `.github/workflows/` | Set up Notion, Import Spotify history, Weekly sync, Backfill, Build Crate, Tests |
| `docs/` | Setup guide and the Claude prompt |

## License

[MIT](LICENSE). Issues and pull requests are welcome.
