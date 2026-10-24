"""Fill in what earlier runs couldn't: exact Spotify IDs (artists, albums, songs' links), photos, covers
and genres. Runs every few hours and is a quick no-op once nothing is missing, so a Spotify rate limit
only delays things instead of needing a manual re-run.

    python sync/backfill.py [--dry-run]
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from musiclib import notion as N  # noqa: E402
from musiclib.deezer import Deezer  # noqa: E402
from musiclib.lastfm import LastFm  # noqa: E402
from musiclib.library import Blocklist, GenreMap, build  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from musiclib.pipeline import (LEDGER, Run, attach_spotify_links, check_releases, enrich, excluded, load_yaml,  # noqa: E402
                               spotify_exact, stats_md, summary)
from musiclib.plays import Ledger  # noqa: E402
from musiclib.spotify import Spotify  # noqa: E402


def missing(run):
    artists = [r for r in run.artist_rows if not excluded(r)]
    albums = [r for r in run.album_rows if not excluded(r)]
    return {
        "artists without a Spotify ID": sum(1 for r in artists if not r.get("Spotify ID")),
        "artists not matched exactly": sum(1 for r in artists if not run.spotify_state.get(r["id"], {}).get("exact")),
        "artists without a photo": sum(1 for r in artists if not r.get("Image")),
        "artists without a genre": sum(1 for r in artists if not r.get("Genres")),
        "albums without a Spotify ID": sum(1 for r in albums if not r.get("Spotify ID")),
        "albums without a cover": sum(1 for r in albums if not r.get("Cover")),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    cfg = load_yaml("config.yml")
    run = Run(cfg, N.Notion(os.environ.get("NOTION_TOKEN", "")),
              LastFm(os.environ.get("LASTFM_API_KEY", ""), cfg["lastfm_user"]),
              Spotify(os.environ.get("SPOTIFY_CLIENT_ID", ""), os.environ.get("SPOTIFY_CLIENT_SECRET", ""), cfg["releases"]["market"]),
              args.dry_run, GenreMap(load_yaml("genre_map.yml")), Blocklist(load_yaml("blocklist.yml")))
    run.deezer = Deezer()
    run.load()
    before = missing(run)
    today = datetime.now(timezone.utc).astimezone(ZoneInfo(cfg["timezone"])).date()
    if not any(v for k, v in before.items() if k != "artists without a genre"):
        check_releases(run, today)   # each artist about once a week; Deezer is quick
        attach_spotify_links(run)
        summary("## Backfill\n\nNothing missing. " + f"{before['artists without a genre']} artists have no genre.\n\n"
                + stats_md(run, "Release checks"))
        return
    ledger = Ledger.load(LEDGER, cfg["timezone"])
    _, artists, albums = build(ledger, run.artist_rows, run.album_rows)
    spotify_exact(run, artists, albums)
    enrich(run, refresh_tags=False)   # fills gaps only; uses stored tags, no full Last.fm refresh
    check_releases(run, today)
    attach_spotify_links(run)
    after = missing(run)
    table = ["| | Before | After |", "|---|---:|---:|"] + [f"| {k} | {before[k]} | {after[k]} |" for k in before]
    summary("\n".join(["## Backfill", ""] + table + [""]) + "\n" + stats_md(run, "Backfill writes" + (" (dry run)" if run.dry_run else "")))


if __name__ == "__main__":
    main()
