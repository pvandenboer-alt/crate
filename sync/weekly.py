"""Weekly sync: Last.fm scrobbles since the last run, new artists and albums, enrichment, levels,
peak years, new releases and the Crate report.

    python sync/weekly.py            # real run
    python sync/weekly.py --dry-run  # report what would change, write nothing
"""
import argparse
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))

from musiclib import notion as N  # noqa: E402
from musiclib.lastfm import LastFm  # noqa: E402
from musiclib.library import Blocklist, GenreMap, build  # noqa: E402
from musiclib.pipeline import (LEDGER, Run, attach_spotify_links, blocked_reason, check_releases, spotify_exact, compute_levels, enrich, excluded, load_yaml,  # noqa: E402
                               stats_md, summary, upsert_albums, upsert_artists)
from musiclib.plays import Ledger, album_key, from_scrobbles, track_id  # noqa: E402
from musiclib import scrobbles as S  # noqa: E402
from musiclib.report import run_report  # noqa: E402
from musiclib.deezer import Deezer  # noqa: E402
from musiclib.spotify import Spotify  # noqa: E402
from musiclib.text import norm  # noqa: E402


MAX_FAILURES = 10  # a few lookups failing shouldn't turn the weekly run red


class HealthCheckFailed(SystemExit):
    pass


def health_check(lastfm, max_age_days: int, now: datetime):
    latest = lastfm.latest_scrobble()
    if latest is None or now - latest > timedelta(days=max_age_days):
        when = latest.strftime("%d %b %Y") if latest else "never"
        msg = (f"## Health check failed\n\nThe latest Last.fm scrobble is from **{when}**, more than "
               f"{max_age_days} days ago. Spotify has probably stopped sending plays to Last.fm. Open "
               "Last.fm → Settings → Applications and reconnect Spotify, then run this workflow again.\n")
        summary(msg)
        raise HealthCheckFailed(msg)
    return latest


def recent_counts(scrobbles, resolver):
    """Plays and distinct tracks per artist key and album key over the scrobbles given."""
    art_plays, art_tracks, alb_tracks = Counter(), {}, {}
    for s in scrobbles:
        res = resolver.resolve(s.artist)
        art_plays[res.key] += 1
        art_tracks.setdefault(res.key, set()).add(track_id(s.track))
        if s.album.strip():
            k = f"{res.key}\t{album_key(s.artist, s.album).split(chr(9))[1]}"
            alb_tracks.setdefault(k, set()).add(track_id(s.track))
    return art_plays, art_tracks, alb_tracks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-health-check", action="store_true")
    args = ap.parse_args()

    cfg = load_yaml("config.yml")
    now = datetime.now(timezone.utc)
    lastfm = LastFm(os.environ.get("LASTFM_API_KEY", ""), cfg["lastfm_user"])
    spotify = Spotify(os.environ.get("SPOTIFY_CLIENT_ID", ""), os.environ.get("SPOTIFY_CLIENT_SECRET", ""),
                      cfg["releases"]["market"])
    run = Run(cfg, N.Notion(os.environ.get("NOTION_TOKEN", "")), lastfm, spotify, args.dry_run,
              GenreMap(load_yaml("genre_map.yml")), Blocklist(load_yaml("blocklist.yml")))
    run.deezer = Deezer()
    do_weekly(run, now, health=not args.skip_health_check)
    if len(run.failures) > MAX_FAILURES:
        sys.exit(f"{len(run.failures)} failures (see the summary).")


def do_weekly(run: Run, now: datetime, health=True, ledger_path=LEDGER):
    cfg, th, lastfm = run.cfg, run.cfg["thresholds"], run.lastfm
    today = now.astimezone(ZoneInfo(cfg["timezone"])).date()

    # 1. Health check.
    if health:
        health_check(lastfm, cfg["health"]["max_scrobble_age_days"], now)

    # 2. Notion rows and the Blocklist, then scrobbles since the last run (and the last 30 days for the "new" thresholds).
    ledger = Ledger.load(ledger_path, cfg["timezone"])
    if not ledger.credits:
        summary("> No import ledger yet (state/ledger.json). Run the importer first for all-time counts; "
                "this run only counts Last.fm scrobbles.\n")
        start = str(cfg.get("scrobbles_from") or "").strip()   # empty: all of your Last.fm history
        ledger.cursor_uts = int(datetime.fromisoformat(start).timestamp()) - 1 if start else 0
    window_start = int((now - timedelta(days=th["recent_days"])).timestamp())
    fetched = from_scrobbles(lastfm.scrobbles(min(ledger.cursor_uts, window_start)))
    newest = max((int(s.start.timestamp()) for s in fetched), default=0)
    run.load()
    fetched = [s for s in fetched if not run.blocklist.song_blocked(s.track, s.artist)]
    fresh = [s for s in fetched if s.start.timestamp() > ledger.cursor_uts]
    recent = [s for s in fetched if s.start.timestamp() >= window_start]
    for s in fresh:
        ledger.add(s)
    ledger.cursor_uts = max(ledger.cursor_uts, newest)

    # 3. Update existing rows and add new ones that pass the weekly thresholds (always as Review).
    resolver, artists, albums = build(ledger, run.artist_rows, run.album_rows)
    art_plays, _, alb_tracks = recent_counts(recent, resolver)
    add_artists, blocked = set(), []
    for k, e in artists.items():
        if e.row or art_plays.get(k, 0) < th["weekly_artist_plays"]:
            continue
        why = blocked_reason(run, e)
        if why:
            blocked.append(f"{e.name} ({why})")
        else:
            add_artists.add(k)
    artist_ids = upsert_artists(run, artists, add_artists, lambda e: "Review", lambda e: False)
    names = {k: (e.row or {}).get("Name") or e.name for k, e in artists.items()}
    add_albums = {k for k, e in albums.items()
                  if not e.row and len(alb_tracks.get(k, ())) >= th["weekly_album_tracks"] and e.artist_key in artist_ids}
    upsert_albums(run, albums, artist_ids, names, add_albums, lambda e: "Review")

    # 4-6. Exact Spotify matches, enrichment, levels and peak years (with the stats above), releases.
    spotify_exact(run, artists, albums)
    enrich(run, refresh_tags=today.day <= 7)   # Last.fm tags change slowly: refresh them on the month's first run
    compute_levels(run)
    check_releases(run, today)
    attach_spotify_links(run)

    # Every scrobble with its time, for the Crate report (next to the ledger: state/scrobbles.jsonl).
    archive = Path(ledger_path).with_name("scrobbles.jsonl")
    scrobble_rows, new_scrobbles = S.merged(archive, fetched)
    if not run.dry_run:
        ledger.save(ledger_path)
        if new_scrobbles:
            S.save(archive, scrobble_rows)

    # 7. Summary.
    head = [f"Scrobbles since the last run: **{len(fresh)}** (last 30 days: {len(recent)}).", ""]
    if blocked:
        head += ["Skipped (block list): " + ", ".join(blocked), ""]
    added = [e.name for k, e in artists.items() if k in add_artists]
    if added:
        head += ["New artists for review: " + ", ".join(sorted(added)), ""]
    summary("\n".join(head) + "\n" + stats_md(run, "Weekly sync" + (" (dry run)" if run.dry_run else "")))

    # 8. The Crate report. A problem here never fails the sync.
    try:
        run_report(run, ledger.credits, scrobble_rows, now)
    except Exception as e:  # noqa: BLE001
        run.fail("Crate report", e)
    return add_artists


if __name__ == "__main__":
    main()
