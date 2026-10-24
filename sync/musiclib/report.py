"""The Crate report: which of Crate's features you use, and which get you listening again. It joins the
Crate log (written by the app) with the scrobble archive and the ledger, and is rewritten every week on
the Crate report page in Notion and in the job summary.

A Play counts as heard when Last.fm (or Crate's own player) has that artist within 3 hours, as a
rediscovery when the artist or album had been quiet for a year, and as stuck when there's more of that
artist one to six weeks later. Rediscoveries per month use the same rule as the history before Crate,
so the two can be compared."""
import bisect
import json
from collections import defaultdict
from datetime import date, datetime
from zoneinfo import ZoneInfo

from . import notion as N
from .library import Resolver
from .pipeline import summary

DAY = 86400
HEARD_WITHIN = 3 * 3600        # a scrobble of the artist this soon after a Play: it was heard
EARLY = 120                    # ...allowing for a scrobble stamped just before the tap
STICK_FROM, STICK_TO = 7, 42   # more of the artist 1-6 weeks after a heard Play: it stuck
QUIET_MONTHS = 12              # a rediscovery: plays again after more than a year without any...
MIN_PRIOR_PLAYS = 20           # ...by an artist with at least this many plays before

FEATURES = {
    "pick": "Today's Pick", "rediscover": "Rediscover", "years-ago": "This Month, Years Ago",
    "on-repeat": "On Repeat", "missed": "Albums You Missed", "start-to-finish": "Start to Finish",
    "revisit": "Albums to Revisit", "browse-library": "Library", "library": "Library",
    "gartists": "Genre: all artists", "galbums": "Genre: all albums", "your-years": "Your Years",
    "year": "Year pages", "month": "Month pages", "genres": "Genres", "genre": "Genre pages",
    "genre-essentials": "Genre: Your Essentials", "genre-songs": "Genre: Top Songs", "genre-albums": "Genre: Albums",
    "genre-rediscover": "Genre: Rediscover", "genre-years": "Genre: Your Years", "heat": "Heat",
    "new": "New Releases", "new-top": "New Releases: top pick", "coming-soon": "Coming Soon",
    "release": "Release page", "release-favourites": "Release: your favourites", "release-albums": "Release: your albums",
    "search": "Search", "artist": "Artist page", "artist-songs": "Artist: Top Songs", "artist-albums": "Artist: Your Albums",
    "similar": "Similar in Your Library", "artist-releases": "Artist: New Releases", "album": "Album page",
    "album-favourites": "Album: Your Favourites", "album-more": "Album: More by", "songs": "Top Songs (all)",
    "repeat": "On Repeat (all)", "whole": "Start to Finish (all)", "player": "Player", "home": "Home",
}
label = lambda s: FEATURES.get(s, s or "Unknown")


def read_events(rows):
    """Events from Crate log rows (N.row dicts). Returns (events oldest first, unreadable rows)."""
    events, bad = [], 0
    for r in rows:
        try:
            batch = json.loads(r.get("Data") or "[]")
        except ValueError:
            bad += 1
            continue
        events.extend(e for e in batch if isinstance(e, dict) and isinstance(e.get("t"), (int, float)) and e.get("e"))
    events.sort(key=lambda e: e["t"])
    return events, bad


def _mi(ym):
    y, m = map(int, ym.split("-"))
    return y * 12 + m - 1


def _ym(i):
    return f"{i // 12}-{i % 12 + 1:02d}"


def _between(times, lo, hi):
    i = bisect.bisect_left(times, lo)
    return i < len(times) and times[i] <= hi


class Facts:
    """Everything the report needs, indexed: artist names, album -> artist, scrobble times per artist."""

    def __init__(self, scrobbles, artist_rows, album_rows, tz):
        self.tz = ZoneInfo(tz)
        clean = lambda i: str(i or "").replace("-", "")
        rows = [{**r, "id": clean(r["id"])} for r in artist_rows]
        self.resolver = Resolver(rows)
        self.names = {r["id"]: r.get("Name") or "?" for r in rows}
        self.album_artist = {clean(r["id"]): clean((r.get("Artist") or [""])[0]) for r in album_rows}
        self.album_names = {clean(r["id"]): r.get("Name") or "?" for r in album_rows}
        self.times = defaultdict(list)    # artist page id -> scrobble times, sorted
        for uts, artist, _, _ in scrobbles:
            aid = self.page(artist)
            if aid:
                self.times[aid].append(int(uts))
        for v in self.times.values():
            v.sort()

    def page(self, credit):
        key = self.resolver.resolve(credit).key
        return key[5:] if key.startswith("page:") else ""

    def day(self, t):
        return datetime.fromtimestamp(t, self.tz).date()

    def artist_of(self, e):
        if e.get("a"):
            return e["a"]
        kind, i = e.get("k"), str(e.get("i") or "").replace("-", "")
        return i if kind == "artist" else self.album_artist.get(i, "") if kind == "album" else ""


def funnel(events, facts, key, now):
    """Per value of key(event): seen, opened, played, heard, rediscovered, stuck / old enough to tell."""
    out = defaultdict(lambda: {"seen": 0, "opened": 0, "played": set(), "heard": set(), "found": set(),
                               "first": {}, "stuck": 0, "matured": 0})
    listens = defaultdict(list)
    for e in events:
        if e["e"] == "listen" and e.get("a"):
            listens[e["a"]].append(e["t"])
    for e in events:
        f = out[key(e)]
        if e["e"] == "seen":
            f["seen"] += len([x for x in str(e.get("i") or "").split(",") if x])
        elif e["e"] == "open":
            f["opened"] += 1
        elif e["e"] == "play":
            t, a = int(e["t"]), facts.artist_of(e)
            k = (a or "item:" + str(e.get("i")), facts.day(t))
            f["played"].add(k)
            if a and (_between(facts.times[a], t - EARLY, t + HEARD_WITHIN) or _between(listens[a], t, t + HEARD_WITHIN)):
                f["heard"].add(k)
                if e.get("q"):
                    f["found"].add(k)
                f["first"].setdefault(a, t)
    for f in out.values():
        for a, t in f["first"].items():
            if now - t >= STICK_TO * DAY:
                f["matured"] += 1
                f["stuck"] += _between(facts.times[a], t + STICK_FROM * DAY, t + STICK_TO * DAY)
    return out


def comebacks(credits, facts):
    """Per artist page: month index -> plays, from the ledger (all history, months in your time zone)."""
    mo = defaultdict(lambda: defaultdict(int))
    for c in credits.values():
        aid = facts.page(c.get("name") or "")
        if aid:
            for ym, n in (c.get("mo") or {}).items():
                mo[aid][_mi(ym)] += n
    found = defaultdict(list)   # month index -> artists back after a quiet year
    for aid, months in mo.items():
        ms, before = sorted(m for m, n in months.items() if n), 0
        for i, m in enumerate(ms):
            if i and m - ms[i - 1] > QUIET_MONTHS and before >= MIN_PRIOR_PLAYS:
                found[m].append(aid)
            before += months[m]
    return mo, found


CONTEXT_MONTHS = 6   # months before Crate shown next to the Crate months, for comparison


def stats(events, scrobbles, artist_rows, album_rows, credits, now: datetime, tz="UTC") -> dict:
    """Every number in the report, as plain data: the page is written from it, and Crate's stats
    screen reads it (as JSON in the page's App data toggle)."""
    facts, t_now = Facts(scrobbles, artist_rows, album_rows, tz), int(now.timestamp())
    local_now = now.astimezone(facts.tz)
    cur = local_now.year * 12 + local_now.month - 1
    start = (facts.day(events[0]["t"]).year * 12 + facts.day(events[0]["t"]).month - 1) if events else cur

    # Rediscoveries per month, before and with Crate.
    mo, found = comebacks(credits, facts)
    stuck = lambda m, aid: bool(mo[aid].get(m + 1) or mo[aid].get(m + 2))
    base = range(start - 12, start)
    ripe = [(m, aid) for m in base if m + 2 < cur for aid in found[m]]
    plays_of = defaultdict(list)
    for e in events:
        if e["e"] == "play" and facts.artist_of(e):
            plays_of[facts.artist_of(e)].append(int(e["t"]))
    months = []
    for m in range(start - CONTEXT_MONTHS, cur + 1):
        lo, hi = (int(datetime((x) // 12, (x) % 12 + 1, 1, tzinfo=facts.tz).timestamp()) for x in (m, m + 1))
        via = 0
        for aid in found[m] if m >= start else ():
            first = next((t for t in facts.times[aid] if lo <= t < hi), None)   # the comeback play itself
            via += first is not None and any(p - EARLY <= first <= p + HEARD_WITHIN for p in plays_of[aid])
        months.append({"m": _ym(m), "n": len(found[m]), "via": via, "stuck": sum(stuck(m, aid) for aid in found[m]),
                       "ripe": m + 2 < cur, "crate": m >= start, "partial": m == cur})
    data = {"v": 1, "updated": local_now.date().isoformat(), "start": _ym(start),
            "baseline": {"per_month": round(sum(len(found[m]) for m in base) / 12, 1),
                         "stick": round(sum(stuck(m, aid) for m, aid in ripe) / len(ripe), 2) if ripe else 0},
            "months": months, "usage": None, "features": [], "starts": [], "back": []}
    if not events:
        return data

    # Usage, then the funnel per feature (where the tap was) and per starting point.
    days = {facts.day(e["t"]) for e in events}
    feat = funnel(events, facts, lambda e: e.get("s") or "", t_now)
    tot = lambda k: sum(len(f[k]) if isinstance(f[k], set) else f[k] for f in feat.values())
    data["usage"] = {"days": len(days), "since": min(days).isoformat(), **{k: tot(k) for k in ("opened", "played", "heard", "found")}}
    data["features"] = _ranked(feat)
    data["starts"] = _ranked(funnel(events, facts, lambda e: e.get("r") or e.get("s") or "", t_now))

    # The artists Crate brought back, newest first.
    for e in reversed(events):
        if e["e"] == "play" and e.get("q"):
            t, a = int(e["t"]), facts.artist_of(e)
            if a and _between(facts.times[a], t - EARLY, t + HEARD_WITHIN) and a not in [x["a"] for x in data["back"]]:
                i = str(e.get("i")).replace("-", "") if e.get("k") == "album" else ""
                data["back"].append({"a": a, "name": facts.names.get(a, "?"), "i": i, "album": facts.album_names.get(i, "") if i else "",
                                     "s": e.get("s") or "", "d": facts.day(t).isoformat()})
        if len(data["back"]) >= 15:
            break
    return data


def _ranked(fun):
    out = []
    for k, f in fun.items():
        row = {"k": k, "label": label(k), "seen": f["seen"], "opened": f["opened"], "played": len(f["played"]),
               "heard": len(f["heard"]), "found": len(f["found"]), "stuck": f["stuck"], "matured": f["matured"]}
        if any(row[x] for x in ("seen", "opened", "played", "heard", "found")):
            out.append(row)
    out.sort(key=lambda r: (-(r["opened"] + r["played"]), r["label"]))
    return out


def parts(data):
    """The report as parts: ("h", text), ("p", text), ("li", text), ("table", header, rows)."""
    b, start = data["baseline"], _mi(data["start"])
    out = [("p", f"Updated {_day(date.fromisoformat(data['updated']))}. Rewritten by the weekly sync from the Crate log, Last.fm "
                 "and your play history. Crate shows the same numbers under Settings → Crate Stats."),
           ("h", "Rediscoveries"),
           ("p", f"Artists back after more than a year without plays (with {MIN_PRIOR_PLAYS}+ plays before). "
                 f"Before Crate: {b['per_month']:.1f} a month over the 12 months before {_label(start)}, and "
                 f"{b['stick']:.0%} of them stuck (played again in the next two months).")]
    rows = [[_label(_mi(m["m"])) + (" (so far)" if m["partial"] else ""), str(m["n"]), str(m["via"]),
             f"{m['stuck']} of {m['n']}" if m["ripe"] and m["n"] else "–"] for m in data["months"] if m["crate"]]
    out.append(("table", ["Month", "Rediscoveries", "Through Crate", "Stuck"], rows))
    if not data["usage"]:
        return out + [("h", "Features"), ("p", "No Crate usage logged yet. Update Crate and use it; the next weekly sync fills this in.")]
    u = data["usage"]
    cells = lambda r: [r["label"], *(str(r[k]) if r[k] else "–" for k in ("seen", "opened", "played", "heard", "found")),
                       f"{r['stuck']} of {r['matured']}" if r["matured"] else "–"]
    head = ["Seen", "Opened", "Played", "Heard", "Rediscovered", "Stuck"]
    out += [("h", "Features"),
            ("p", f"Crate used on {u['days']} days since {_day(date.fromisoformat(u['since']))}: {u['opened']} pages opened, "
                  f"{u['played']} Plays, {u['heard']} heard, {u['found']} of them rediscoveries."),
            ("p", "Seen: picks shown on the shelf (counted once a day). Opened: taps that opened a page. Played: Plays started "
                  "(one per artist per day). Heard: Last.fm or Crate's player had that artist within 3 hours. "
                  "Rediscovered: heard, after a quiet year. Stuck: of heard Plays at least 6 weeks old, how many "
                  "were followed by more of that artist 1 to 6 weeks later."),
            ("table", ["Feature", *head], [cells(r) for r in data["features"]]),
            ("h", "Where visits start"),
            ("p", "The same, credited to the Home, Heat, New or Search shelf the visit began on, however many pages deep the Play was."),
            ("table", ["Started from", *head], [cells(r) for r in data["starts"]])]
    if data["back"]:
        out.append(("h", "Back from the quiet"))
        out += [("li", f"{x['name']}{' – ' + x['album'] if x['album'] else ''}: {label(x['s'])}, {_day(date.fromisoformat(x['d']), year=False)}")
                for x in data["back"]]
    return out


def build(events, scrobbles, artist_rows, album_rows, credits, now: datetime, tz="UTC"):
    return parts(stats(events, scrobbles, artist_rows, album_rows, credits, now, tz))


def _day(d, year=True):
    return f"{d.day} {d:%b %Y}" if year else f"{d.day} {d:%b}"


def _label(m):
    return datetime(m // 12, m % 12 + 1, 1).strftime("%b %Y")


# ---------- output ----------
def markdown(parts, title="Crate report"):
    out = [f"## {title}", ""]
    for kind, *rest in parts:
        if kind == "h":
            out += [f"### {rest[0]}", ""]
        elif kind == "p":
            out += [rest[0], ""]
        elif kind == "li":
            out.append(f"- {rest[0]}")
        elif kind == "table":
            head, rows = rest
            out += ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)] + ["| " + " | ".join(r) + " |" for r in rows] + [""]
    return "\n".join(out) + "\n"


APP_DATA = "App data"   # the toggle heading Crate reads the stats from


def app_data_block(data):
    """A collapsed "App data" heading holding the stats as a JSON code block (2000-character pieces)."""
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    pieces = [{"type": "text", "text": {"content": text[i:i + 2000]}} for i in range(0, len(text), 2000)][:100]
    return {"object": "block", "type": "heading_3", "heading_3": {
        "rich_text": [{"type": "text", "text": {"content": APP_DATA}}], "is_toggleable": True, "children": [
            {"object": "block", "type": "code", "code": {"language": "json", "rich_text": pieces}}]}}


def blocks(parts):
    rt = lambda s: [{"type": "text", "text": {"content": str(s)[:2000]}}]
    out = []
    for kind, *rest in parts:
        if kind == "h":
            out.append({"object": "block", "type": "heading_2", "heading_2": {"rich_text": rt(rest[0])}})
        elif kind == "p":
            out.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": rt(rest[0])}})
        elif kind == "li":
            out.append({"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": rt(rest[0])}})
        elif kind == "table":
            head, rows = rest
            out.append({"object": "block", "type": "table", "table": {
                "table_width": len(head), "has_column_header": True, "has_row_header": False,
                "children": [{"object": "block", "type": "table_row", "table_row": {"cells": [rt(c) for c in r]}}
                             for r in [head, *rows][:100]]}})
    return out


def write_page(notion, page_id, new_blocks):
    """Replace the page's content (its own blocks; sub-pages and databases stay)."""
    old, cursor = [], None
    while True:
        d = notion.request("GET", f"/blocks/{page_id}/children?page_size=100" + (f"&start_cursor={cursor}" if cursor else ""))
        old += [b for b in d.get("results", []) if b.get("type") not in ("child_page", "child_database")]
        if not d.get("has_more"):
            break
        cursor = d.get("next_cursor")
    for b in old:
        notion.request("DELETE", f"/blocks/{b['id']}")
    for i in range(0, len(new_blocks), 100):
        notion.request("PATCH", f"/blocks/{page_id}/children", {"children": new_blocks[i:i + 100]})


def run_report(run, credits, scrobble_rows, now):
    """The weekly step: read the Crate log, build the report, show it in the summary and write the page."""
    ds, page = run.ds.get("crate_log"), run.ds.get("crate_report")
    if not ds:
        return None
    events, bad = read_events(N.row(p) for p in run.notion.query_all(ds))
    data = stats(events, scrobble_rows, run.artist_rows, run.album_rows, credits, now, run.cfg["timezone"])
    out = parts(data)
    if bad:
        out.append(("p", f"{bad} Crate log rows couldn't be read and were left out."))
    summary(markdown(out))
    if page and not run.dry_run:
        write_page(run.notion, page, blocks(out) + [app_data_block(data)])
    return data
