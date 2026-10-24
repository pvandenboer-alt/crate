# Set up Crate with Claude

Copy everything in the box below into [Claude](https://claude.ai) (or Claude Code, which can also work on your copy of the repository directly). Fill in the two lines at the top first. Claude then guides you one step at a time, and can help you change Crate afterwards.

````text
I want to set up Crate, an open-source music library and Android app, for myself:
https://github.com/pvandenboer-alt/crate

About me (fill in):
- My phone: Android
- What I have so far: [e.g. "nothing yet" / "Spotify export requested" / "a private copy on GitHub"]

Please be my setup guide. The full instructions are in docs/SETUP.md in that repository; read it (and the
README) first if you can open links or the repository, and follow its order:
1. Request the Spotify Extended Streaming History export
2. Last.fm: scrobbling from Spotify, an API key, my username
3. A Spotify developer app (Client ID and secret, redirect URI crate-music://spotify, Web API + Android,
   Android package io.github.pvandenboer_alt.crate with the fingerprint from SETUP.md, me under User
   Management)
4. Notion: a "Music Library" page and an internal integration with read, update and insert content,
   connected to that page
5. My own PRIVATE copy of the repository (Use this template), the four repository secrets, the three YOURS
   lines in sync/config.yml, enabling Actions, and running the Set up Notion workflow
6. Slimming my export with sync/slim_export.py, uploading it to data/, a dry-run import, then the real import
7. Installing Crate.apk, connecting Notion, and adding my Spotify Client ID for playback

How to guide me:
- One step at a time. Tell me exactly where to click and what I should see, then wait until I say it's done
  or show you a screenshot. Check each step worked before moving on.
- Explain in plain language what each key is for and where it goes. Keys only ever go into GitHub's
  repository secrets or the Crate app on my phone.
- Never ask me to paste a secret (Notion integration secret, Last.fm API key, Spotify client secret) into
  this chat, a file, a commit, an issue or a pull request. If I start to, stop me. The Spotify Client ID is
  not a secret, but it still doesn't belong in the repository.
- My copy of the repository must stay private: it will hold my listening history. Remind me if I'm about
  to make it public.
- If something fails, ask for the exact error (the GitHub Actions run summary or the grey text in Crate's
  Settings) and use the Troubleshooting table in SETUP.md before guessing.
- For the export: help me run sync/slim_export.py on my own computer (or, if you can run code, on files I
  give you) so my IP address, devices, country and podcasts never get uploaded. Explain any command before
  I run it.
- After the dry-run import, help me read the summary: do the top artists look right, are there duplicates
  that need an alias, should a threshold change, is anything there I want to block?

When everything works, I may want to change Crate to suit me (genres, thresholds, the blocklist, the
weekly time, or the app itself). Then:
- Work in my private copy, keep the tests passing (python -m pytest -q sync/tests), and explain what you
  change and why.
- Keep the security model: no keys in the app or the repository, workflows that refuse to run in a public
  repository, and no personal data in anything I might share.
````

## What Claude can and can't do here

- **Claude (claude.ai)** reads your screenshots and the error messages you paste, and explains every step. It can't click in your accounts for you.
- **Claude Code** can also clone your private copy, run `sync/slim_export.py` and the tests on your computer, and make changes to your copy (for example to `sync/config.yml` or `sync/genre_map.yml`) as commits you review.
- Neither needs your keys. If a guide, a person or a tool asks you to paste a secret into a chat, don't.
