# Crate

A music library in Notion built from your Spotify and Last.fm history, and an Android app for rediscovering it.
Anyone can set up their own copy from this repo (see `docs/SETUP.md`).

## Layout

- `sync/`: Python 3.12 importer, weekly sync, backfill and `setup.py` (creates the Notion databases and writes their
  IDs into `config.yml`). The importer can work from Last.fm alone.
- `app/`: the Capacitor 8 app (`io.github.pvandenboer_alt.crate`). `android/` is generated in CI.
- `docs/`: setup guide and the Claude setup prompt.

## Rules

- No keys or IDs in the APK. The Notion key, database IDs and Spotify client ID are entered in the app and stored on
  the phone. The app finds its databases by marker columns (same markers as `sync/musiclib/notion.py` DATABASES).
- Every workflow that commits refuses to run in a public repo, and quietly skips when `NOTION_TOKEN` is absent. Keep
  both guards.
- `config.yml` ships with empty personal fields.
- Actions are pinned to commit SHAs. Keep them pinned; Dependabot updates the SHAs.
- Spotify dev mode rate-limits heavy use. The client paces calls and stops for the run when blocked; don't add retries.

## Workflows and release

- `app.yml`: on pushes to `main` that touch `app/`, builds the app and updates the rolling release `crate`
  (`Crate.apk`, version 1.N where N is the run number).
- `tests.yml`: `python -m pytest -q sync/tests` on pushes and PRs that touch `sync/`.
- `setup.yml`, `import.yml`, `sync.yml`, `backfill.yml`: run in a user's own copy.

## Maintenance

- A weekly maintenance check may open pull requests; it never pushes to `main`, merges or creates releases.
- Dependabot opens monthly update PRs (minor and patch grouped, majors one at a time). Merging one ships a new build.
- Docs-only commits to `main` can carry `[skip ci]` in the message so they don't build a release.
- This repo is public. Nothing personal goes in commits, PRs, issues or files: no names, emails (commit with the
  GitHub noreply address), Notion page or database IDs, tokens, usernames or locations.
