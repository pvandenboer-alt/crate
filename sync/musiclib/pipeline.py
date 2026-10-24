"""Steps shared by the importer and the weekly sync: load Notion, upsert rows, enrich, levels, releases."""
import json
import os
import re
import time
import traceback
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml

from . import notion as N
from .library import (Blocklist, Entity, GenreMap, build, credited_as, levels, tags_text)
from .lastfm import clean_bio
from .spotify import SpotifyBlocked, album_type, is_live, release_date
from .text import clean_title, loose, norm, split_aliases

ROOT = Path(__file__).resolve().parents[2]
SYNC = ROOT / "sync"
LEDGER = ROOT / "state" / "ledger.json"
GENRES = ROOT / "state" / "genres.json"   # genres the sync set itself, per artist page
SPOTIFY = ROOT / "state" / "spotify.json"  # per artist page: matched exactly, Spotify's genres, name clash


def load_yaml(name):
    return yaml.safe_load((SYNC / name).read_text(encoding="utf-8")) or {}


def summary(md: str):
    """Append Markdown to the GitHub job summary (and echo it to the log)."""
    print(md)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(md + "\n")


@dataclass
class Run:
    cfg: dict
    notion: N.Notion | None
    lastfm: object = None
    spotify: object = None
    dry_run: bool = True
    genre_map: GenreMap = None
    blocklist: Blocklist = None
    artist_rows: list = field(default_factory=list)
    album_rows: list = field(default_factory=list)
    release_rows: list = field(default_factory=list)
    stats: dict = field(default_factory=lambda: {k: 0 for k in (
        "artists_added", "artists_updated", "albums_added", "albums_updated", "enriched_artists",
        "enriched_albums", "levels_changed", "new_releases", "writes")})
    failures: list = field(default_factory=list)
    tag_cache: dict = field(default_factory=dict)
    genres_path: Path = GENRES
    genre_changes: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    spotify_path: Path = SPOTIFY
    spotify_state: dict = field(default_factory=dict)
    no_genre: list = field(default_factory=list)
    deezer: object = None        # optional image fallback (keyword-only in practice: keep it last)
    deezer_path: Path = None     # state/deezer.json unless set

    def spotify_ok(self):
        return bool(self.spotify) and not getattr(self.spotify, "blocked", "")

    def note(self, text):
        if text not in self.notes:
            self.notes.append(text)
            print("note:", text)

    @property
    def ds(self):
        return self.cfg["notion"]

    def fail(self, what: str, err: Exception):
        self.failures.append(f"{what}: {err}")
        print(f"! {what}: {err}")
        if os.environ.get("DEBUG"):
            traceback.print_exc()

    # ---- notion I/O ----
    def load(self):
        missing = [k for k in N.SCHEMA if not (self.ds.get(k) or "").strip()]
        if missing:
            raise N.NotionError("sync/config.yml has no Notion database IDs yet (" + ", ".join(missing)
                                + "). Run the 'Set up Notion' workflow first.")
        self.notion.validate(self.ds)
        self.artist_rows = [N.row(p) for p in self.notion.query_all(self.ds["artists"])]
        self.album_rows = [N.row(p) for p in self.notion.query_all(self.ds["albums"])]
        self.release_rows = [N.row(p) for p in self.notion.query_all(self.ds["new_releases"])]
        self.blocklist.add_rows(N.row(p) for p in self.notion.query_all(self.ds["blocklist"]))
        load_spotify_state(self)
        for r in self.artist_rows + self.album_rows:
            r["id"] = r["id"].replace("-", "")

    def write(self, row_id, props):
        if not props:
            return
        self.stats["writes"] += 1
        if not self.dry_run:
            self.notion.update(row_id, props)

    def create(self, ds_key, props) -> str:
        self.stats["writes"] += 1
        if self.dry_run:
            return "dry" + str(self.stats["writes"])
        return self.notion.create(self.ds[ds_key], props)["id"].replace("-", "")

    # ---- last.fm tags (cached per run) ----
    def tags(self, name):
        if name not in self.tag_cache:
            try:
                self.tag_cache[name] = self.lastfm.top_tags(name) if self.lastfm else []
            except Exception as e:  # noqa: BLE001 - one artist failing shouldn't stop the run
                self.fail(f"tags for {name}", e)
                self.tag_cache[name] = []
        return self.tag_cache[name]


def blocked_reason(run: "Run", e: Entity) -> str:
    """Why a new artist must not be added ('' when it may): block list name, strongest tag or main genre."""
    if run.blocklist.name_blocked(e.name):
        return "on the block list"
    tags = run.tags(e.name) if run.lastfm else []
    tag = run.blocklist.tag_blocked(tags)
    if tag:
        return f"top tag '{tag}'"
    en = run.cfg["enrichment"]
    genre = run.blocklist.genre_blocked(run.genre_map.genres_for(tags, en["tag_min_weight"], en["max_genres"]))
    return f"genre {genre} is blocked" if genre else ""


def years_text(years) -> str:
    return "; ".join(f"{y}:{n}" for y, n in sorted(years.items()) if n)


def excluded(row) -> bool:
    return bool(row) and row.get("Status") == "Excluded"


def sources(src: set, current=None):
    out = set(current or [])
    if "export" in src:
        out.add("Spotify export")
    if "lastfm" in src:
        out.add("Last.fm")
    return sorted(out)


def append_note(current: str, note: str) -> str:
    current = current or ""
    return current if note in current else (current + "\n" + note).strip()


def stat_props(e: Entity, row: dict | None, *, album=False):
    p = {
        "Plays": N.number(e.plays),
        "Peak year": N.number(e.peak_year),
        "First listened": N.date(e.first),
        "Last listened": N.date(e.last),
        "Source": N.multi(sources(e.src, (row or {}).get("Source"))),
        "Plays by year": N.text(years_text(e.years)),
    }
    if not album and e.songs:
        p["Top songs"] = N.text(e.top_songs())
    if not album and e.months:
        p["Plays by month"] = N.text(e.months_text())
    if album:
        p["Full listens"] = N.number(e.full)
        p["Distinct tracks played"] = N.number(len(e.tracks))
        variants = set(split_aliases((row or {}).get("Title variants"))) | e.variants
        p["Title variants"] = N.text("; ".join(sorted(variants)))
    # Never move dates backwards or plays down when the row already knows more (e.g. manual edits).
    if row:
        if row.get("First listened") and e.first and str(row["First listened"])[:10] < e.first:
            p.pop("First listened")
        if row.get("Last listened") and e.last and str(row["Last listened"])[:10] > e.last:
            p.pop("Last listened")
    return p


def multi_note(e: Entity) -> str:
    multi = sorted(c for c, r in e.rules.items() if r == "multi")
    if not multi:
        return ""
    return "Check: multi-artist credit(s) attributed here: " + "; ".join(multi)


def upsert_artists(run: Run, artists: dict, add_keys: set, status_for, watch_for, level_for=lambda e: None):
    """Create artists in add_keys and refresh stats on every matched, non-excluded row.
    Returns {entity key: notion page id} for all artists now in the library."""
    ids = {}
    for key, e in sorted(artists.items(), key=lambda kv: -kv[1].plays):
        try:
            if e.row:
                if excluded(e.row):
                    continue
                ids[key] = e.row["id"]
                props = stat_props(e, e.row)
                note = multi_note(e)
                if note:
                    props["Notes"] = N.text(append_note(e.row.get("Notes"), note))
                ch = N.changes(e.row, props)
                if ch:
                    run.write(e.row["id"], ch)
                    e.row.update({k: N.read({"type": next(iter(v)), **v}) for k, v in ch.items()})
                    run.stats["artists_updated"] += 1
            elif key in add_keys:
                props = {"Name": N.title(e.name), "Status": N.select(status_for(e)),
                         "Watch releases": N.checkbox(watch_for(e)), **stat_props(e, None)}
                note = multi_note(e)
                if note:
                    props["Notes"] = N.text(note)
                if level_for(e):   # set now rather than in a second pass over every new row
                    props["Level"] = N.select(level_for(e))
                pid = run.create("artists", props)
                e.row = {"id": pid, "Name": e.name, "Status": status_for(e), "Plays": e.plays, "Aliases": "",
                         "Genres": [], "Similar artists": [], "Image": None, "Spotify ID": "", "Tags": "",
                         "Watch releases": watch_for(e), "Notes": note, "Level": level_for(e)}
                run.artist_rows.append(e.row)
                ids[key] = pid
                run.stats["artists_added"] += 1
        except Exception as ex:  # noqa: BLE001
            run.fail(f"artist {e.name}", ex)
    return ids


def upsert_albums(run: Run, albums: dict, artist_ids: dict, artist_names: dict, add_keys: set, status_for,
                  level_for=lambda e: None):
    for k, e in sorted(albums.items(), key=lambda kv: -kv[1].plays):
        aid = artist_ids.get(e.artist_key)
        try:
            if e.row:
                if excluded(e.row):
                    continue
                props = stat_props(e, e.row, album=True)
                if aid and not e.row.get("Artist"):
                    props["Artist"] = N.relation([aid])
                ch = N.changes(e.row, props)
                if ch:
                    run.write(e.row["id"], ch)
                    e.row.update({kk: N.read({"type": next(iter(v)), **v}) for kk, v in ch.items()})
                    run.stats["albums_updated"] += 1
            elif k in add_keys and aid and not run.blocklist.album_blocked(e.name, artist_names.get(e.artist_key, "")):
                props = {"Name": N.title(e.name), "Artist": N.relation([aid]), "Status": N.select(status_for(e)),
                         **stat_props(e, None, album=True)}
                credit = credited_as(e, artist_names.get(e.artist_key, ""))
                if credit:
                    props["Credited as"] = N.text(credit)
                if is_live(e.name):
                    props["Type"] = N.select("Live")
                if level_for(e):
                    props["Level"] = N.select(level_for(e))
                pid = run.create("albums", props)
                e.row = {"id": pid, "Name": e.name, "Artist": [aid], "Status": status_for(e), "Plays": e.plays,
                         "Cover": None, "Spotify ID": "", "Type": "Live" if is_live(e.name) else None,
                         "Release date": None, "Credited as": credit, "Level": level_for(e)}
                run.album_rows.append(e.row)
                run.stats["albums_added"] += 1
        except Exception as ex:  # noqa: BLE001
            run.fail(f"album {e.name}", ex)


def compute_levels(run: Run):
    """Rank Keep and Review rows by plays (artists among artists, albums among albums)."""
    lv = run.cfg["levels"]
    for rows in (run.artist_rows, run.album_rows):
        live = [r for r in rows if r.get("Status") in ("Keep", "Review")]
        new = levels([(r["id"], r.get("Plays") or 0) for r in live], lv["a_whole_lot"], lv["quite_a_bit"])
        for r in live:
            if r.get("Level") != new[r["id"]]:
                run.write(r["id"], {"Level": N.select(new[r["id"]])})
                r["Level"] = new[r["id"]]
                run.stats["levels_changed"] += 1


def enrich(run: Run, *, refresh_tags=True):
    """Fill missing genres, similar artists, images and covers; refresh Tags and the sync's own genres."""
    en = run.cfg["enrichment"]
    try:
        auto = json.loads(Path(run.genres_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        auto = {}
    library = [r for r in run.artist_rows if not excluded(r)]
    index = {}
    for r in library:
        index.setdefault(norm(r.get("Name")), r["id"])
        for a in split_aliases(r.get("Aliases")):
            index.setdefault(norm(a), r["id"])

    for r in library:
        name, props = r.get("Name") or "", {}
        try:
            exact = run.spotify_state.get(r["id"], {})
            clash = exact.get("collision", False)   # Last.fm describes another act with the same name
            tags = []
            if run.lastfm and (refresh_tags or not r.get("Tags")):
                tags = run.tags(name)
                if tags:
                    props["Tags"] = N.text(tags_text(tags, en["tags_kept"]))
            tags = tags or parse_tags(r.get("Tags"))
            # Spotify's genres for the exact artist come first. Last.fm tags belong to a name, so they're
            # only used when no other act shares it; weaker tags are a last resort.
            g = run.genre_map.genres_for([(x, 100) for x in exact.get("genres") or []], 0, en["max_genres"], curated=True)
            if not g and not clash:
                g = (run.genre_map.genres_for(tags, en["tag_min_weight"], en["max_genres"])
                     or run.genre_map.genres_for(tags, 10, en["max_genres"])
                     or run.genre_map.genres_for(tags, 1, 1))   # barely tagged: one genre at most
            cur = r.get("Genres") or []
            # Genres the sync set itself follow genre_map.yml; anything else was set by hand and stays.
            # Rows without a record yet (set before this was tracked) count as the sync's.
            mine = r["id"] not in auto or sorted(auto[r["id"]]) == sorted(cur)
            if g and (not cur or (mine and sorted(g) != sorted(cur))):
                props["Genres"] = N.multi(g)
                auto[r["id"]] = g
                if cur:
                    run.genre_changes.append((name, cur, g))
            elif cur and mine:
                auto[r["id"]] = cur
            if run.lastfm and not r.get("Similar artists"):
                sim = []
                for sname, match in run.lastfm.similar(name, 100):
                    sid = index.get(norm(sname))
                    if sid and sid != r["id"] and sid not in sim:
                        sim.append(sid)
                    if len(sim) >= en["similar_kept"]:
                        break
                if sim:
                    props["Similar artists"] = N.relation(sim)
            if r.get("Bio") and re.search(r"<[a-z/][^>]*>|&[a-z]+;", r["Bio"]):   # tidy bios saved before the HTML fix
                props["Bio"] = N.text(clean_bio(r["Bio"]))
            if run.lastfm and not clash and (not r.get("Bio") or not r.get("MusicBrainz ID")):
                bio, mbid = run.lastfm.artist_info(name)
                if bio and not r.get("Bio"):
                    props["Bio"] = N.text(bio)
                if mbid and not r.get("MusicBrainz ID"):
                    props["MusicBrainz ID"] = N.text(mbid)
            if run.spotify_ok() and (not r.get("Image") or not r.get("Spotify ID")):
                try:
                    sp = run.spotify.artist(r["Spotify ID"]) if r.get("Spotify ID") else \
                        run.spotify.find_artist(name, split_aliases(r.get("Aliases")))
                except SpotifyBlocked as sb:
                    sp = None
                    run.note(f"{sb}: the rest of this run skips Spotify; Deezer supplies photos and covers, "
                             "and the next run fills in Spotify links.")
                if sp:
                    if not r.get("Spotify ID"):
                        props["Spotify ID"] = N.text(sp["id"])
                    img = run.spotify.image(sp)
                    if img and not r.get("Image"):
                        props["Image"] = N.url(img)
            if run.deezer and not r.get("Image") and "Image" not in props:
                img = run.deezer.artist_image(name, split_aliases(r.get("Aliases")))
                if img:
                    props["Image"] = N.url(img)
            ch = N.changes(r, props)
            if ch:
                run.write(r["id"], ch)
                r.update({k: N.read({"type": next(iter(v)), **v}) for k, v in ch.items()})
                run.stats["enriched_artists"] += 1
        except Exception as ex:  # noqa: BLE001
            run.fail(f"enrich artist {name}", ex)
    # Still no genre: borrow the main genre most of its similar artists share (two or more agreeing).
    by_id = {r["id"]: r for r in library}
    for r in library:
        if r.get("Genres"):
            continue
        votes = Counter(((by_id.get(s.replace("-", "")) or {}).get("Genres") or [None])[0] for s in r.get("Similar artists") or [])
        votes.pop(None, None)
        top = votes.most_common(1)
        if top and top[0][1] >= 2:
            run.write(r["id"], {"Genres": N.multi([top[0][0]])})
            r["Genres"] = auto[r["id"]] = [top[0][0]]
            run.genre_changes.append((r.get("Name") or "", ["none"], [top[0][0] + " (from similar artists)"]))
        else:
            run.no_genre.append((r.get("Name") or "", ", ".join(t for t, _ in parse_tags(r.get("Tags"))[:4]) or "no tags"))
    save_spotify_state(run)
    if not run.dry_run:
        Path(run.genres_path).parent.mkdir(parents=True, exist_ok=True)
        Path(run.genres_path).write_text(json.dumps(auto, ensure_ascii=False, sort_keys=True, indent=0) + "\n", encoding="utf-8")

    names = {r["id"]: r.get("Name") or "" for r in run.artist_rows}
    for r in run.album_rows:
        if excluded(r) or (r.get("Cover") and r.get("Spotify ID") and r.get("Type") and r.get("Release date")):
            continue
        aid = (r.get("Artist") or [""])[0].replace("-", "")
        artist = r.get("Credited as") or names.get(aid, "")
        if not artist:
            continue
        props = {}
        try:
            if run.lastfm and not r.get("Cover"):
                cover = run.lastfm.album_cover(artist, r.get("Name") or "")
                if not cover:
                    for v in split_aliases(r.get("Title variants")):
                        cover = run.lastfm.album_cover(artist, v)
                        if cover:
                            break
                if cover:
                    props["Cover"] = N.url(cover)
            sp = None
            if run.spotify_ok() and not r.get("Spotify ID"):
                try:
                    sp = run.spotify.find_album(artist, r.get("Name") or "")
                    if not sp and artist != names.get(aid):
                        sp = run.spotify.find_album(names.get(aid, ""), r.get("Name") or "")
                except SpotifyBlocked as sb:
                    run.note(f"{sb}: the rest of this run skips Spotify; Deezer supplies photos and covers, "
                             "and the next run fills in Spotify links.")
                if sp:
                    props["Spotify ID"] = N.text(sp["id"])
                    if not r.get("Type"):
                        props["Type"] = N.select(album_type(sp))
                    if not r.get("Release date") and release_date(sp):
                        props["Release date"] = N.date(release_date(sp))
                    if not r.get("Cover") and "Cover" not in props and run.spotify.image(sp):
                        props["Cover"] = N.url(run.spotify.image(sp))
            if run.deezer and not r.get("Cover") and "Cover" not in props:
                cover = run.deezer.album_cover(artist, r.get("Name") or "")
                if cover:
                    props["Cover"] = N.url(cover)
            ch = N.changes(r, props)
            if ch:
                run.write(r["id"], ch)
                r.update({k: N.read({"type": next(iter(v)), **v}) for k, v in ch.items()})
                run.stats["enriched_albums"] += 1
        except Exception as ex:  # noqa: BLE001
            run.fail(f"enrich album {r.get('Name')}", ex)


def parse_tags(text):
    out = []
    for part in str(text or "").split(";"):
        name, _, w = part.strip().rpartition(":")
        if name:
            out.append((name.strip(), int(w) if w.strip().isdigit() else 0))
    return out


def load_spotify_state(run: Run):
    """Per-artist Spotify matches, plus when an earlier run was told to stay away (so no run asks
    again before then: knocking while blocked makes Spotify extend the block)."""
    try:
        run.spotify_state = json.loads(Path(run.spotify_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        run.spotify_state = {}
    until = float(run.spotify_state.get("_blocked_until") or 0)
    if run.spotify and until > time.time() and not run.spotify.blocked:
        run.spotify.blocked_until = until
        run.spotify.blocked = "Spotify rate limit until " + datetime.fromtimestamp(until, timezone.utc).strftime("%-d %b %H:%M UTC")
        run.note(run.spotify.blocked + ": this run skips Spotify.")


def save_spotify_state(run: Run):
    if run.spotify and getattr(run.spotify, "blocked_until", 0) > time.time():
        run.spotify_state["_blocked_until"] = run.spotify.blocked_until
    if not run.dry_run:
        Path(run.spotify_path).parent.mkdir(parents=True, exist_ok=True)
        Path(run.spotify_path).write_text(json.dumps(run.spotify_state, ensure_ascii=False, sort_keys=True, indent=0) + "\n",
                                          encoding="utf-8")


def spotify_exact(run: Run, artists: dict, albums: dict):
    """Exact Spotify IDs from tracks you actually played (one request covers 50), instead of searching by
    name. Fills artist and album IDs, photos, covers, release dates and Spotify's genres, and corrects
    artists a name search matched to someone else (a rapper and a metal band can share a name).
    Works 50 tracks at a time and saves as it goes, so a run that hits its request budget (or a rate
    limit) keeps what it found and the next run carries on."""
    if not run.spotify_ok():
        return
    st = run.spotify_state
    todo_a = [e for e in artists.values() if e.row and not excluded(e.row) and e.track_ids and not st.get(e.row["id"], {}).get("exact")]
    todo_al = [e for e in albums.values() if e.row and not excluded(e.row) and e.track_ids and not e.row.get("Spotify ID")]
    # One track per album, and per artist the next of their tracks not tried yet: most artists are
    # matched through an album's track, so they rarely need a request of their own.
    want = [e.track_ids[0] for e in todo_al]
    for e in todo_a:
        k = st.get(e.row["id"], {}).get("tries", 0)
        if k < len(e.track_ids):
            want.append(e.track_ids[k])
    want = list(dict.fromkeys(want))
    if not want:
        return
    seen, done_a, done_al, fixed = {}, set(), set(), []
    try:
        for n in range(0, len(want), 50):
            chunk = want[n:n + 50]
            seen.update(run.spotify.tracks(chunk))
            matched = {}
            for e in todo_a:
                pid = e.row["id"]
                if pid in done_a:
                    continue
                names = {loose(e.name), *(loose(c) for c in e.credits), *(loose(a) for a in split_aliases(e.row.get("Aliases")))}
                hit = None
                for t in e.track_ids:
                    hit = next((a for a in (seen.get(t) or {}).get("artists", []) if loose(a.get("name")) in names), None)
                    if hit:
                        break
                if hit:
                    matched[pid] = (e, hit["id"])
                    done_a.add(pid)
                elif any(t in chunk for t in e.track_ids):
                    st.setdefault(pid, {})["tries"] = st.get(pid, {}).get("tries", 0) + 1
            if matched:
                details = run.spotify.artists([sid for _, sid in matched.values()])
                for pid, (e, sid) in matched.items():
                    if exact_artist(run, e, sid, details.get(sid) or {}):
                        fixed.append(e.row.get("Name") or "")
            for e in todo_al:
                tr = next((seen[t] for t in e.track_ids if t in seen), None)
                if tr and e.row["id"] not in done_al:
                    done_al.add(e.row["id"])
                    exact_album(run, e, tr)
            save_spotify_state(run)
    except SpotifyBlocked as sb:
        run.note(f"{sb}: exact Spotify matching carries on in the next run.")
    if fixed:
        run.note("Matched to the right artist on Spotify (a name search had found another act with the same name): "
                 + ", ".join(sorted(fixed)))
    save_spotify_state(run)


def exact_artist(run: Run, e, sid: str, d: dict) -> bool:
    """Write an artist's exact Spotify match; True when it corrected a wrong (same-name) match."""
    r, st = e.row, run.spotify_state
    clash = bool(r.get("Spotify ID")) and r["Spotify ID"] != sid
    props = {"Spotify ID": N.text(sid)}
    img = run.spotify.image(d) if d else ""
    if img and (clash or not r.get("Image") or "dzcdn.net" in (r.get("Image") or "")):
        props["Image"] = N.url(img)
    if clash:
        props["Bio"] = N.text("")   # it described the other act
    st[r["id"]] = {"exact": True, "genres": d.get("genres") or [], "collision": clash or st.get(r["id"], {}).get("collision", False)}
    ch = N.changes(r, props)
    if ch:
        run.write(r["id"], ch)
        r.update({k: N.read({"type": next(iter(v)), **v}) for k, v in ch.items()})
        run.stats["enriched_artists"] += 1
    return clash


def exact_album(run: Run, e, tr: dict):
    al = tr.get("album") or {}
    if not al.get("id"):
        return
    r, props = e.row, {"Spotify ID": N.text(al["id"])}
    if not r.get("Type"):
        props["Type"] = N.select(album_type(al))
    if not r.get("Release date") and release_date(al):
        props["Release date"] = N.date(release_date(al))
    if not r.get("Cover") and run.spotify.image(al):
        props["Cover"] = N.url(run.spotify.image(al))
    ch = N.changes(r, props)
    if ch:
        run.write(r["id"], ch)
        r.update({k: N.read({"type": next(iter(v)), **v}) for k, v in ch.items()})
        run.stats["enriched_albums"] += 1


RELEASE_TYPES = {"Album", "EP", "Live"}   # singles and compilations aren't listed
RELEASE_SHARE = 0.35                       # watch the top 35% of artists by plays (Inferno, Blaze, Flame)
RECHECK_DAYS = 6


def watched_artists(run: Run):
    """Artists whose releases are checked: the top 35% by plays (the app's Inferno, Blaze and Flame),
    plus any with Watch releases ticked by hand."""
    live = [r for r in run.artist_rows if not excluded(r) and r.get("Status") in ("Keep", "Review")]
    ranked = sorted(live, key=lambda r: -(r.get("Plays") or 0))
    top = {r["id"] for r in ranked[:max(1, round(len(ranked) * RELEASE_SHARE))]}
    return [r for r in live if r["id"] in top or r.get("Watch releases")]


DEEZER = ROOT / "state" / "deezer.json"   # artist page -> Deezer artist ID


# Radio shows, DJ mixes and podcast episodes that catalogues file as albums (a DJ's weekly
# "A State of Trance Episode 1297"): not new music, so never listed.
NOT_RELEASES = re.compile(r"\bepisode\s*\d+|\ba state of trance\b|\basot\s*\d+|radio show|podcast|"
                          r"\byear mix\b|\(dj mix\)|\bmixed by\b|continuous mix|\bmixtape vol", re.I)


def deezer_type(a) -> str:
    """Deezer's record_type as the Notion Type ('' for singles and compilations, which aren't listed)."""
    t = a.get("record_type")
    if t not in ("album", "ep") or NOT_RELEASES.search(a.get("title") or ""):
        return ""
    return "Live" if is_live(a.get("title", "")) else ("EP" if t == "ep" else "Album")


DEEZER_VERSION = 3   # 2: artist IDs proven by a shared album or song (1 trusted the name alone); 3: language check


def top_song_titles(row) -> list:
    try:
        return [x[0] for x in json.loads(row.get("Top songs") or "[]") if x]
    except (ValueError, TypeError, IndexError):
        return []


def known_text(row, albums) -> str:
    """Everything you've played by an artist, as text: their song and album titles."""
    try:
        songs = [" ".join(map(str, x[:2])) for x in json.loads(row.get("Top songs") or "[]") if x]
    except (ValueError, TypeError):
        songs = []
    return " ".join([row.get("Name") or "", row.get("Aliases") or "", *albums, *songs])


def release_info(dz: dict, today: date) -> dict:
    """The New Releases properties that come from Deezer's full album: date, cover, size, label, tracks."""
    tracks = [[t.get("title") or "", t.get("duration") or 0] for t in (dz.get("tracks") or {}).get("data", [])]
    while tracks and len(json.dumps(tracks, ensure_ascii=False)) > 1900:
        tracks.pop()
    return {
        "Release date": N.date(dz.get("release_date")), "Found on": N.date(today.isoformat()),
        "Cover": N.url(dz.get("cover_xl") or dz.get("cover_big") or ""),
        "Tracks": N.number(dz.get("nb_tracks") or len(tracks) or None),
        "Length": N.number(round((dz.get("duration") or 0) / 60) or None),
        "Label": N.text(dz.get("label") or ""), "Track list": N.text(json.dumps(tracks, ensure_ascii=False) if tracks else ""),
        "Deezer ID": N.text(str(dz.get("id") or "")),
    }


AHEAD_DAYS = 120   # announced albums up to four months out are listed as Coming Soon; later dates are placeholders
BOX_SET_TRACKS = 30   # 30+ tracks: a best-of, box set or label compilation, not a new album
NOT_NEW = re.compile(r"\binstrumentals?\b|\bacapellas?\b|\bkaraoke\b|bande originale|\bsoundtrack\b", re.I)


def rare_letters(text) -> set:
    """Letters beyond Western European ones (Turkish İ ş ğ, Cyrillic, Greek, Asian scripts...)."""
    return {c.lower() for c in str(text or "") if c.isalpha() and ord(c) > 0xFF}


def skip_reason(a: dict, full: dict, dz, older: set, today: date, since: str, known_text: str = "") -> str:
    """Why a Deezer release isn't a new album or EP worth listing ('' when it is). Deezer sometimes
    merges two acts with one name into one profile (two acts called the same in different countries): a release whose
    titles use letters none of your songs by them have is taken to be the other act's."""
    title, when = a.get("title") or "", full.get("release_date") or a.get("release_date") or ""
    words = " ".join([title] + [t.get("title") or "" for t in (full.get("tracks") or {}).get("data", [])])
    if not deezer_type(a):
        return "a single, compilation, mix or radio episode"
    if when < since:
        return "an older album (re-listed in the catalogue)"
    if when > (today + timedelta(days=AHEAD_DAYS)).isoformat():
        return f"a placeholder date ({when})"
    if str((full.get("artist") or {}).get("id") or dz) != str(dz):
        return "someone else's album they appear on"
    if (full.get("nb_tracks") or 0) >= BOX_SET_TRACKS:
        return "a best-of or box set"
    if NOT_NEW.search(title):
        return "an instrumental, karaoke or soundtrack edition"
    if title != clean_title(title) and norm(clean_title(title)) in older:
        return "a reissue of an older album"
    if known_text and rare_letters(words) - rare_letters(known_text):
        return "probably another act with the same name (different language)"
    return ""


def check_releases(run: Run, today: date, limit: int | None = None):
    """Add albums and EPs from watched artists, found on Deezer (fast, no key, generous limits). Spotify
    links are attached afterwards by attach_spotify_links. Each artist is checked about once a week."""
    if not run.deezer:
        return
    for r in list(run.release_rows):   # tidy up mixes and episodes listed before this filter existed
        if r.get("id") and r.get("Status") == "New" and NOT_RELEASES.search(r.get("Name") or ""):
            trash_release(run, r, "a mix or radio episode, not a release")
    rc = run.cfg["releases"]
    path = Path(run.deezer_path or DEEZER)
    try:
        ids = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ids = {}
    albums_by_artist = {}
    for al in run.album_rows:
        for aid in al.get("Artist") or []:
            albums_by_artist.setdefault(aid.replace("-", ""), []).append(al.get("Name") or "")
    by_id = {r["id"]: r for r in run.artist_rows}
    first_since = (today - timedelta(days=rc["first_check_days"])).isoformat()

    def deezer_id(r):
        if r["id"] not in ids:
            ids[r["id"]] = run.deezer.find_artist(r.get("Name") or "", split_aliases(r.get("Aliases")),
                                                  albums_by_artist.get(r["id"], []), top_song_titles(r)) or ""
        return ids[r["id"]]

    if ids.get("_v") != DEEZER_VERSION:
        # Earlier IDs matched on name alone. Prove them again, and take back releases by a namesake.
        ids = {"_v": DEEZER_VERSION}
        listed = {}
        for rel in run.release_rows:
            if rel.get("id") and rel.get("Status") == "New" and rel.get("Artist"):
                listed.setdefault(rel["Artist"][0].replace("-", ""), []).append(rel)
        for aid, rels in listed.items():
            artist = by_id.get(aid)
            try:
                dz = deezer_id(artist) if artist else ""
                cat = run.deezer.albums(dz) if dz else []
                older = older_titles(cat, albums_by_artist.get(aid, []), first_since)
                for rel in rels:
                    a = next((x for x in cat if norm(x.get("title")) == norm(rel.get("Name"))), None)
                    if not a:
                        trash_release(run, rel, f"by another artist called {artist.get('Name') if artist else 'that'}")
                        continue
                    full = run.deezer.album(a["id"]) or {}
                    why = skip_reason(a, full, dz, older, today, first_since, known_text(artist, albums_by_artist.get(aid, [])))
                    if why:
                        trash_release(run, rel, why)
                    else:
                        run.write(rel["id"], {k: v for k, v in release_info({**a, **full}, today).items() if k != "Found on"})
            except Exception as ex:  # noqa: BLE001
                run.fail(f"checking Deezer releases for {artist.get('Name') if artist else aid}", ex)

    known_names = {(tuple(a.replace("-", "") for a in r.get("Artist") or []), norm(clean_title(r.get("Name"))))
                   for r in run.release_rows}
    due_by = (today - timedelta(days=RECHECK_DAYS)).isoformat()
    due = sorted((r for r in watched_artists(run) if str(r.get("Last release check") or "")[:10] <= due_by),
                 key=lambda r: str(r.get("Last release check") or ""))
    if limit is not None and len(due) > limit:
        run.note(f"Checked releases for {limit} of {len(due)} artists due; the rest follow in the next runs.")
        due = due[:limit]
    for r in due:
        # Never look back further than a month: a long gap between checks shouldn't list old albums.
        since = max(str(r.get("Last release check") or "")[:10], first_since)
        try:
            dz = deezer_id(r)
            cat = run.deezer.albums(dz) if dz else []
            older = older_titles(cat, albums_by_artist.get(r["id"], []), first_since)
            for a in cat:
                kind, title = deezer_type(a), a.get("title") or ""
                key = ((r["id"],), norm(clean_title(title)))
                if not kind or (a.get("release_date") or "") < since or key in known_names:
                    continue
                full = run.deezer.album(a["id"]) or {}
                if skip_reason(a, full, dz, older, today, first_since, known_text(r, albums_by_artist.get(r["id"], []))):
                    continue
                props = {"Name": N.title(title or "Untitled"), "Artist": N.relation([r["id"]]),
                         "Type": N.select(kind), "Status": N.select("New"), **release_info({**a, **full}, today)}
                pid = run.create("new_releases", props)
                known_names.add(key)
                run.release_rows.append({"id": pid, "Artist": [r["id"]], "Name": title, "Spotify ID": "", "Status": "New"})
                run.stats["new_releases"] += 1
            run.write(r["id"], {"Last release check": N.date(today.isoformat())})
        except Exception as ex:  # noqa: BLE001
            run.fail(f"releases for {r.get('Name')}", ex)
    if not run.dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(ids, sort_keys=True, indent=0) + "\n", encoding="utf-8")


def older_titles(catalogue, library_albums, since: str) -> set:
    """Album titles the artist already had before this month, so a 'Deluxe' or 'Anniversary' edition
    of one of them reads as a reissue."""
    return ({norm(clean_title(x.get("title"))) for x in catalogue if (x.get("release_date") or "") < since}
            | {norm(clean_title(t)) for t in library_albums}) - {""}


def trash_release(run: Run, r: dict, why: str):
    run.stats["writes"] += 1
    if not run.dry_run:
        run.notion.request("PATCH", f"/pages/{r['id']}", {"in_trash": True})
    if r in run.release_rows:
        run.release_rows.remove(r)
    run.note(f"Removed '{r.get('Name')}' from New Releases ({why}).")


def attach_spotify_links(run: Run):
    """New releases found on Deezer get their exact Spotify album when Spotify is available."""
    if not run.spotify_ok():
        return
    by_id = {r["id"]: r for r in run.artist_rows}
    for r in list(run.release_rows):
        if r.get("Spotify ID") or not r.get("id") or r.get("Status") not in (None, "New"):
            continue
        aid = ((r.get("Artist") or [""])[0]).replace("-", "")
        artist = (by_id.get(aid) or {}).get("Name") or ""
        try:
            sp = run.spotify.find_album(artist, r.get("Name") or "") if artist else None
        except SpotifyBlocked as sb:
            run.note(f"{sb}: Spotify links for new releases follow on the next run.")
            return
        except Exception as ex:  # noqa: BLE001
            run.fail(f"Spotify link for {r.get('Name')}", ex)
            continue
        mine, theirs = (by_id.get(aid) or {}).get("Spotify ID") or "", {x.get("id") for x in (sp or {}).get("artists") or []} - {None}
        if mine and theirs and mine not in theirs:
            trash_release(run, r, "on Spotify it's by another artist with the same name")
            continue
        if sp:
            run.write(r["id"], {"Spotify ID": N.text(sp["id"]),
                                "Spotify link": N.url((sp.get("external_urls") or {}).get("spotify") or f"https://open.spotify.com/album/{sp['id']}")})
            r["Spotify ID"] = sp["id"]


def stats_md(run: Run, title: str) -> str:
    s = run.stats
    lines = [f"## {title}", "", "| | |", "|---|---:|",
             f"| Artists added | {s['artists_added']} |", f"| Artists updated | {s['artists_updated']} |",
             f"| Albums added | {s['albums_added']} |", f"| Albums updated | {s['albums_updated']} |",
             f"| Artists enriched | {s['enriched_artists']} |", f"| Albums enriched | {s['enriched_albums']} |",
             f"| Level changes | {s['levels_changed']} |", f"| New releases | {s['new_releases']} |",
             f"| Notion writes{' (not sent: dry run)' if run.dry_run else ''} | {s['writes']} |", ""]
    if run.notes:
        lines += ["### Notes", ""] + [f"- {n}" for n in run.notes] + [""]
    if run.no_genre:
        lines += [f"### Artists without a genre ({len(run.no_genre)})", "",
                  "Their tags don't map to a genre yet. Add a tag to sync/genre_map.yml or set genres in Crate.", ""]
        lines += [f"- {n}: {t}" for n, t in sorted(run.no_genre)[:80]] + [""]
    if run.genre_changes:
        lines += [f"### Genre changes ({len(run.genre_changes)})", ""]
        lines += [f"- {n}: {', '.join(a)} → {', '.join(b)}" for n, a, b in run.genre_changes[:100]]
        lines.append("")
    if run.failures:
        lines += [f"### Failures ({len(run.failures)})", ""] + [f"- {f}" for f in run.failures[:50]]
        if len(run.failures) > 50:
            lines.append(f"- … and {len(run.failures) - 50} more (see the log)")
    else:
        lines.append("No failures.")
    return "\n".join(lines) + "\n"
