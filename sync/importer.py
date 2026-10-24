"""One-off import of the Spotify Extended Streaming History, merged with Last.fm scrobbles.

    python sync/importer.py              # dry run: report only, nothing written
    python sync/importer.py --write      # real run: upsert Artists, Albums, then enrich
"""
import argparse
import difflib
import glob
import re
import os
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from musiclib import notion as N  # noqa: E402
from musiclib.lastfm import LastFm  # noqa: E402
from musiclib.library import (LEVELS, Blocklist, GenreMap, album_passes, artist_passes, build, levels)  # noqa: E402
from musiclib.pipeline import (LEDGER, ROOT, Run, blocked_reason, compute_levels, spotify_exact, enrich, excluded, load_yaml, stats_md,  # noqa: E402
                               summary, upsert_albums, upsert_artists)
from musiclib.plays import Ledger, from_scrobbles, merge, read_export  # noqa: E402
from musiclib.deezer import Deezer  # noqa: E402
from musiclib.spotify import Spotify  # noqa: E402
from musiclib.text import norm  # noqa: E402


def scrobbles_start(cfg, export) -> datetime:
    """Last.fm scrobbles count from this moment: scrobbles_from in config.yml, or else right after the
    export's last play (overlaps would be deduplicated anyway)."""
    if str(cfg.get("scrobbles_from") or "").strip():
        return datetime.fromisoformat(str(cfg["scrobbles_from"]))
    if not export:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)   # no export: all of your Last.fm history
    return max(p.start for p in export)


def md_escape(s):
    return str(s).replace("|", "\\|").replace("\n", " ")


def suspected_duplicates(names):
    """Pairs of artist names that are probably the same act (Postman/Postmen, 'The X'/'X')."""
    keys = sorted({norm(n): n for n in names}.items())
    out, seen = [], set()
    stripped = {}
    for k, n in keys:
        s = k[4:] if k.startswith("the ") else k
        if s in stripped and stripped[s] != n:
            out.append((stripped[s], n, "differ only by 'The'"))
        stripped.setdefault(s, n)
    by_len = sorted(keys, key=lambda kv: len(kv[0]))
    for i, (a, na) in enumerate(by_len):
        if len(a) < 4:
            continue
        for b, nb in by_len[i + 1:]:
            if len(b) - len(a) > 2:
                break
            if (a, b) in seen:
                continue
            r = difflib.SequenceMatcher(None, a, b).ratio()
            if r >= 0.88:
                seen.add((a, b))
                out.append((na, nb, f"{r:.0%} similar"))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="real run (default is a dry run)")
    ap.add_argument("--data", default=str(ROOT / "data"), help="folder with Streaming_History_Audio_*.json")
    ap.add_argument("--no-scrobbles", action="store_true", help="skip merging Last.fm scrobbles")
    args = ap.parse_args()
    dry = not args.write

    cfg = load_yaml("config.yml")
    th = cfg["thresholds"]
    files = sorted(glob.glob(os.path.join(args.data, "**", "Streaming_History_Audio_*.json"), recursive=True))
    if not files and args.no_scrobbles:
        sys.exit(f"No Streaming_History_Audio_*.json files found in {args.data}, and --no-scrobbles leaves nothing to import.")
    if not files:
        summary(f"> No Spotify export in `{args.data}`: importing your whole Last.fm history instead.\n")

    lastfm = LastFm(os.environ["LASTFM_API_KEY"], cfg["lastfm_user"]) if os.environ.get("LASTFM_API_KEY") else None
    spotify = None
    if os.environ.get("SPOTIFY_CLIENT_ID") and os.environ.get("SPOTIFY_CLIENT_SECRET"):
        spotify = Spotify(os.environ["SPOTIFY_CLIENT_ID"], os.environ["SPOTIFY_CLIENT_SECRET"], cfg["releases"]["market"])
    if not dry and not (lastfm and spotify):
        sys.exit("A real import needs LASTFM_API_KEY, SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET for enrichment.")
    run = Run(cfg, N.Notion(os.environ.get("NOTION_TOKEN", "")), lastfm, spotify, dry,
              GenreMap(load_yaml("genre_map.yml")), Blocklist(load_yaml("blocklist.yml")))
    run.deezer = Deezer()
    do_import(run, files, use_scrobbles=not args.no_scrobbles)
    if len(run.failures) > 10:
        sys.exit(f"{len(run.failures)} failures (see the summary).")


def do_import(run: Run, files, use_scrobbles=True, ledger_path=LEDGER):
    cfg, th, dry, lastfm = run.cfg, run.cfg["thresholds"], run.dry_run, run.lastfm
    # 1. Read Notion (rows, aliases, Blocklist), parse the export and merge scrobbles.
    run.load()
    export, xstats = read_export(files)
    since = scrobbles_start(cfg, export)
    scrobbles, dupes = [], 0
    if lastfm and use_scrobbles:
        raw = lastfm.scrobbles(int(since.timestamp()) - 1)
        scrobbles = from_scrobbles(raw)
    plays, dupes = merge(export, scrobbles)
    n_before = len(plays)
    plays = [p for p in plays if not run.blocklist.song_blocked(p.track, p.artist)]
    xstats["blocked_songs"] = n_before - len(plays)
    ledger = Ledger(cfg["timezone"])
    for p in plays:
        ledger.add(p)
    ledger.cursor_uts = max([int(s.start.timestamp()) for s in scrobbles] or [int(since.timestamp())])

    # 2. Match against Notion and aggregate.
    resolver, artists, albums = build(ledger, run.artist_rows, run.album_rows)

    # 3. Decide what enters the library.
    blocked = []
    candidates = {}
    for k, e in artists.items():
        if e.row:
            continue
        if not artist_passes(e, th):
            continue
        why = blocked_reason(run, e)
        if why:
            blocked.append((e, why))
        else:
            candidates[k] = e
    add_artists = set(candidates)
    in_library = {k for k, e in artists.items() if e.row and not excluded(e.row)} | add_artists
    album_ok = {k: e for k, e in albums.items() if not e.row and album_passes(e, th) and e.artist_key in in_library}
    album_orphans = [e for e in albums.values() if not e.row and album_passes(e, th) and e.artist_key not in in_library]

    # Levels decide the status of new rows: rank existing Keep/Review rows together with new ones.
    lv = cfg["levels"]
    art_level = levels([(k, e.plays) for k, e in artists.items()
                        if k in add_artists or (e.row and e.row.get("Status") in ("Keep", "Review"))]
                       + [("page:" + r["id"], r.get("Plays") or 0) for r in run.artist_rows
                          if r.get("Status") in ("Keep", "Review") and ("page:" + r["id"]) not in artists],
                       lv["a_whole_lot"], lv["quite_a_bit"])
    alb_level = levels([(k, e.plays) for k, e in albums.items()
                        if k in album_ok or (e.row and e.row.get("Status") in ("Keep", "Review"))],
                       lv["a_whole_lot"], lv["quite_a_bit"])

    def artist_status(e):
        return "Keep" if art_level.get(e.key) in LEVELS[:2] else "Review"

    def album_status(e):
        return "Keep" if alb_level.get(e.key) in LEVELS[:2] else "Review"

    # 4. Report.
    report = [f"# Music Library import {'dry run' if dry else 'run'}", ""]
    first = min((p.start for p in plays), default=None)
    last = max((p.start for p in plays), default=None)
    report += [
        "## Totals", "", "| | |", "|---|---:|",
        f"| Export files | {xstats['files']} |",
        f"| Export rows | {xstats['rows']:,} |",
        f"| Counted plays (30 s or more) | {xstats['plays']:,} |",
        f"| Skipped: under 30 s | {xstats['short']:,} |",
        f"| Skipped: podcasts and audiobooks | {xstats['podcast']:,} |",
        f"| Skipped: no track | {xstats['no_track']:,} |",
        f"| Skipped: blocked songs | {xstats['blocked_songs']:,} |",
        f"| Last.fm scrobbles since {since.date()} | {len(scrobbles):,} |",
        f"| … already in the export (deduplicated) | {dupes:,} |",
        f"| Plays in total | {len(plays):,} |",
        f"| Period | {first.date() if first else '–'} to {last.date() if last else '–'} |",
        f"| Distinct credits / artists after roll-up | {len(ledger.credits):,} / {len(artists):,} |",
        f"| Distinct albums | {len(albums):,} |", "",
    ]
    if not lastfm:
        report += ["> Last.fm key not available: scrobbles weren't merged and tag-based blocking was skipped.", ""]

    new_artists = [e for e in artists.values() if not e.row]
    by_plays = sum(1 for e in new_artists if e.plays >= th["artist_plays"])
    by_tracks = sum(1 for e in new_artists if len(e.tracks) >= th["artist_tracks"])
    either = sum(1 for e in new_artists if artist_passes(e, th))
    report += [
        "## Thresholds", "",
        f"Artists: **{th['artist_plays']}+ plays or {th['artist_tracks']}+ distinct tracks**. "
        f"Albums: **{th['album_tracks']}+ distinct tracks and {th['album_plays']}+ plays**.", "",
        "| | Count |", "|---|---:|",
        f"| New artists with {th['artist_plays']}+ plays | {by_plays} |",
        f"| New artists with {th['artist_tracks']}+ distinct tracks | {by_tracks} |",
        f"| New artists passing (either rule) | {either} |",
        f"| … blocked | {len(blocked)} |",
        f"| **Artists to add** | **{len(add_artists)}** |",
        f"| Existing Notion artists matched | {sum(1 for e in artists.values() if e.row)} |",
        f"| **Albums to add** | **{len(album_ok)}** |",
        f"| Albums passing but artist not in library (skipped) | {len(album_orphans)} |", "",
        "How the artist count moves with the thresholds (plays rule OR tracks rule):", "",
        "| Plays ↓ / Tracks → | 3 | 5 | 8 | 12 |", "|---|---:|---:|---:|---:|",
    ]
    for pl in (10, 20, 30, 50, 100):
        cells = [sum(1 for e in new_artists if e.plays >= pl or len(e.tracks) >= tr) for tr in (3, 5, 8, 12)]
        report.append(f"| {pl} | " + " | ".join(map(str, cells)) + " |")
    report += ["", "Albums (tracks AND plays):", "", "| Plays ↓ / Tracks → | 2 | 3 | 4 | 6 |", "|---|---:|---:|---:|---:|"]
    for pl in (5, 8, 12, 20):
        cells = [sum(1 for e in albums.values() if not e.row and e.plays >= pl and len(e.tracks) >= tr) for tr in (2, 3, 4, 6)]
        report.append(f"| {pl} | " + " | ".join(map(str, cells)) + " |")

    def level_of(e):
        return art_level.get(e.key, "")

    report += ["", "## Top 100 artists", "", "| # | Artist | Plays | Tracks | Peak | Level | Status |",
               "|---:|---|---:|---:|---:|---|---|"]
    top = sorted((e for e in artists.values() if not excluded(e.row)), key=lambda e: -e.plays)[:100]
    for i, e in enumerate(top, 1):
        status = (e.row.get("Status") + " (in Notion)") if e.row else \
            (artist_status(e) + " (new)" if e.key in add_artists else "blocked" if any(b[0] is e for b in blocked) else "–")
        report.append(f"| {i} | {md_escape(e.name)} | {e.plays:,} | {len(e.tracks)} | {e.peak_year or ''} | "
                      f"{level_of(e)} | {status} |")

    names = {k: e.name for k, e in artists.items()}
    report += ["", "## Top 100 albums", "", "| # | Album | Artist | Plays | Tracks | Peak |", "|---:|---|---|---:|---:|---:|"]
    for i, e in enumerate(sorted(albums.values(), key=lambda e: -e.plays)[:100], 1):
        report.append(f"| {i} | {md_escape(e.name)} | {md_escape(names.get(e.artist_key, ''))} | {e.plays:,} | "
                      f"{len(e.tracks)} | {e.peak_year or ''} |")

    rollups = sorted(((c, e.name, e.rules[c], p) for e in artists.values() for c, p in e.credits.items()
                      if e.rules.get(c) in ("alias", "multi")), key=lambda x: -x[3])
    report += ["", "## Alias and multi-artist roll-ups", ""]
    if rollups:
        report += ["| Credit in the data | Counted under | Why | Plays |", "|---|---|---|---:|"]
        report += [f"| {md_escape(c)} | {md_escape(n)} | {'alias' if r == 'alias' else 'first listed artist (flagged in Notes)'} | {p:,} |"
                   for c, n, r, p in rollups[:150]]
    else:
        report.append("None.")

    report += ["", "## Blocked", ""]
    report += [f"- {md_escape(e.name)} ({e.plays:,} plays): {why}" for e, why in sorted(blocked, key=lambda b: -b[0].plays)] or ["None."]

    lib_names = [e.name for k, e in artists.items() if k in in_library]
    dups = suspected_duplicates(lib_names)
    report += ["", "## Suspected duplicate artists", "",
               "Add one as an alias of the other in Notion (Aliases, separated by ;) before the real run.", ""]
    report += [f"- {md_escape(a)} / {md_escape(b)} ({why})" for a, b, why in dups[:100]] or ["None found."]
    alb_dups = []
    by_artist = {}
    for k, e in album_ok.items():
        by_artist.setdefault(e.artist_key, []).append(e)
    sequel = re.compile(r"\b(\d+(\.\d+)?|[ivx]+|[a-d]|pt|part|vol|volume|side|kant)\b")
    for es in by_artist.values():
        for i, a in enumerate(es):
            for b in es[i + 1:]:
                if sequel.sub("", norm(a.name)).split() == sequel.sub("", norm(b.name)).split():
                    continue   # "Tha Carter III" / "Tha Carter IV" are different albums
                if difflib.SequenceMatcher(None, norm(a.name), norm(b.name)).ratio() >= 0.85:
                    alb_dups.append((names.get(a.artist_key, ""), a.name, b.name))
    report += ["", "### Suspected duplicate albums", ""]
    report += [f"- {md_escape(ar)}: {md_escape(a)} / {md_escape(b)}" for ar, a, b in alb_dups[:100]] or ["None found."]
    summary("\n".join(report) + "\n")

    if dry:
        summary("\n**Dry run: nothing was written to Notion.** Check the thresholds above, then run the import "
                "again with dry_run unticked.\n")
        return report

    # 5. Real run: artists, albums, levels, enrichment; then save the ledger for the weekly sync.
    artist_ids = upsert_artists(run, artists, add_artists, artist_status,
                                lambda e: art_level.get(e.key) in LEVELS[:2], lambda e: art_level.get(e.key))
    names = {k: (e.row or {}).get("Name") or e.name for k, e in artists.items()}
    upsert_albums(run, albums, artist_ids, names, set(album_ok), album_status, lambda e: alb_level.get(e.key))
    compute_levels(run)
    spotify_exact(run, artists, albums)
    enrich(run)
    ledger.save(ledger_path)
    summary(stats_md(run, "Import result"))
    return report


if __name__ == "__main__":
    main()
