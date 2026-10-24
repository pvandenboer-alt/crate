"""Small Notion API client: throttled to ~3 requests/second, retries 429 and 5xx, and validates the
database schema before anything is written."""
import time

import requests

API = "https://api.notion.com/v1"
VERSION = "2025-09-03"

# The Notion databases, as the Set up Notion workflow (setup.py) creates them. Every property the jobs
# read or write, with its type and (for selects) its options; relations name the database they point at.
# Crate finds each database by its "marker" columns, so renaming a database in Notion is fine.
_STATUS, _SOURCE = ["Keep", "Review", "Excluded"], ["Spotify export", "Last.fm", "Manual"]
_LEVEL, _TYPE = ["A whole lot", "Quite a bit", "A bit"], ["Album", "EP", "Single", "Live", "Compilation"]
DATABASES = {
    "artists": {
        "title": "Artists", "icon": "🎤", "marker": ["Top songs", "Plays by month", "Aliases"],
        "props": {
            "Name": ("title",), "Status": ("select", _STATUS), "Watch releases": ("checkbox",),
            "Source": ("multi_select", _SOURCE), "Aliases": ("rich_text",), "Plays": ("number",),
            "Level": ("select", _LEVEL), "Genres": ("multi_select", []), "Tags": ("rich_text",),
            "Similar artists": ("relation", "artists"), "Image": ("url",), "Peak year": ("number",),
            "First listened": ("date",), "Last listened": ("date",), "Spotify ID": ("rich_text",),
            "MusicBrainz ID": ("rich_text",), "Last release check": ("date",), "Notes": ("rich_text",),
            "Albums": ("relation", "albums"), "New releases": ("relation", "new_releases"), "Bio": ("rich_text",),
            "Plays by year": ("rich_text",), "Top songs": ("rich_text",), "Plays by month": ("rich_text",),
        },
    },
    "albums": {
        "title": "Albums", "icon": "💿", "marker": ["Full listens", "Distinct tracks played"],
        "props": {
            "Name": ("title",), "Artist": ("relation", "artists", "Albums"), "Credited as": ("rich_text",),
            "Status": ("select", _STATUS), "Source": ("multi_select", _SOURCE), "Type": ("select", _TYPE),
            "Release date": ("date",), "Distinct tracks played": ("number",), "Plays": ("number",),
            "Level": ("select", _LEVEL), "Peak year": ("number",), "Cover": ("url",), "First listened": ("date",),
            "Last listened": ("date",), "Title variants": ("rich_text",), "Spotify ID": ("rich_text",),
            "Notes": ("rich_text",), "Plays by year": ("rich_text",), "Full listens": ("number",),
        },
    },
    "new_releases": {
        "title": "New Releases", "icon": "✨", "marker": ["Found on", "Track list"],
        "props": {
            "Name": ("title",), "Artist": ("relation", "artists", "New releases"), "Credited as": ("rich_text",),
            "Release date": ("date",), "Type": ("select", _TYPE), "Status": ("select", ["New", "Listened", "Saved", "Not interested"]),
            "Found on": ("date",), "Spotify link": ("url",), "Spotify ID": ("rich_text",), "Cover": ("url",),
            "Tracks": ("number",), "Length": ("number",), "Label": ("rich_text",), "Track list": ("rich_text",),
            "Deezer ID": ("rich_text",),
        },
    },
    "blocklist": {
        "title": "Blocklist", "icon": "🚫", "marker": ["Type:select", "Artist:rich_text"],
        "props": {"Name": ("title",), "Type": ("select", ["Artist", "Album", "Song", "Genre"]), "Artist": ("rich_text",)},
    },
    "crate_log": {
        "title": "Crate log", "icon": "📈", "marker": ["Events", "Data", "Sent"],
        "props": {"Name": ("title",), "Sent": ("date",), "Events": ("number",), "Data": ("rich_text",)},
    },
}
REPORT_TITLE = "Crate report"   # a page the weekly sync rewrites; Crate reads its App data for Crate Stats

# Every property the jobs read or write, with its expected Notion type (checked before anything is written).
SCHEMA = {k: {name: spec[0] for name, spec in d["props"].items()} for k, d in DATABASES.items() if k != "crate_log"}


def looks_like(props: dict, key: str) -> bool:
    """Whether a data source's properties carry this database's marker columns ("Name" or "Name:type")."""
    for m in DATABASES[key]["marker"]:
        name, _, typ = m.partition(":")
        if name not in props or (typ and (props[name] or {}).get("type") != typ):
            return False
    return True


def property_body(spec, ids: dict) -> dict:
    """A property definition for creating a database (relations need the target's data source ID)."""
    kind = spec[0]
    if kind in ("select", "multi_select"):
        return {kind: {"options": [{"name": o} for o in spec[1]]}}
    if kind == "number":
        return {"number": {"format": "number"}}
    if kind == "relation":
        target, synced = spec[1], (spec[2] if len(spec) > 2 else None)
        if synced:
            return {"relation": {"data_source_id": ids[target], "type": "dual_property",
                                 "dual_property": {"synced_property_name": synced}}}
        return {"relation": {"data_source_id": ids[target], "type": "single_property", "single_property": {}}}
    return {kind: {}}


class NotionError(RuntimeError):
    pass


class SchemaError(NotionError):
    pass


class Notion:
    def __init__(self, token: str, min_interval: float = 0.35, session=None):
        if not token:
            raise NotionError("NOTION_TOKEN is not set.")
        self.s = session or requests.Session()
        self.s.headers.update({
            "Authorization": f"Bearer {token}", "Notion-Version": VERSION, "Content-Type": "application/json",
        })
        self.min_interval = min_interval
        self._last = 0.0
        self.calls = 0

    def request(self, method: str, path: str, body=None):
        for attempt in range(8):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            self.calls += 1
            try:
                r = self.s.request(method, API + path, json=body, timeout=60)
            except requests.RequestException as e:
                if attempt < 5:
                    time.sleep(2 ** attempt)
                    continue
                raise NotionError(f"{method} {path}: {e}") from e
            if r.status_code == 429:
                time.sleep(float(r.headers.get("Retry-After") or 1) + 0.5)
                continue
            if r.status_code >= 500 and attempt < 5:
                time.sleep(2 ** attempt)
                continue
            if not r.ok:
                try:
                    msg = r.json().get("message", r.text)
                except ValueError:
                    msg = r.text
                raise NotionError(f"{method} {path} -> {r.status_code}: {msg}")
            return r.json()
        raise NotionError(f"{method} {path}: gave up after repeated rate limiting")

    # ---- schema ----
    def data_source(self, ds_id: str):
        return self.request("GET", f"/data_sources/{ds_id}")

    def validate(self, ds_ids: dict) -> dict:
        """Check each database has every property the jobs use, with the right type. Returns the
        live property definitions (including select options) keyed by database."""
        problems, live = [], {}
        for key, expected in SCHEMA.items():
            ds = self.data_source(ds_ids[key])
            props = ds.get("properties", {})
            live[key] = props
            for name, typ in expected.items():
                if name not in props:
                    problems.append(f"{key}: property '{name}' is missing")
                elif props[name].get("type") != typ:
                    problems.append(f"{key}: property '{name}' is {props[name].get('type')}, expected {typ}")
        if problems:
            raise SchemaError("Notion schema doesn't match what the sync expects:\n  - " + "\n  - ".join(problems))
        return live

    # ---- rows ----
    def query_all(self, ds_id: str, filter=None):
        out, cursor = [], None
        while True:
            body = {"page_size": 100}
            if cursor:
                body["start_cursor"] = cursor
            if filter:
                body["filter"] = filter
            d = self.request("POST", f"/data_sources/{ds_id}/query", body)
            out.extend(p for p in d.get("results", []) if p.get("object") == "page" and not p.get("in_trash"))
            if not d.get("has_more"):
                return out
            cursor = d.get("next_cursor")

    def create(self, ds_id: str, properties: dict):
        return self.request("POST", "/pages", {"parent": {"type": "data_source_id", "data_source_id": ds_id},
                                               "properties": properties})

    def update(self, page_id: str, properties: dict):
        return self.request("PATCH", f"/pages/{page_id}", {"properties": properties})


# ---- property value helpers (reading) ----
def _text(rt):
    return "".join(t.get("plain_text") or t.get("text", {}).get("content", "") for t in rt or [])


def read(prop):
    if not prop:
        return None
    t = prop.get("type")
    v = prop.get(t)
    if t in ("title", "rich_text"):
        return _text(v)
    if t == "select":
        return v["name"] if v else None
    if t == "multi_select":
        return [o["name"] for o in v or []]
    if t == "date":
        return v["start"] if v else None
    if t == "relation":
        return [r["id"] for r in v or []]
    return v  # number, checkbox, url


def row(page) -> dict:
    d = {k: read(v) for k, v in page.get("properties", {}).items()}
    d["id"] = page["id"]
    return d


# ---- property value helpers (writing) ----
def title(s):
    return {"title": [{"type": "text", "text": {"content": str(s)[:2000]}}]}


def text(s):
    s = str(s or "")
    return {"rich_text": [{"type": "text", "text": {"content": s[:2000]}}] if s else []}


def number(n):
    return {"number": n}


def select(name):
    return {"select": {"name": name} if name else None}


def multi(names):
    return {"multi_select": [{"name": n} for n in names]}


def date(d):
    return {"date": {"start": str(d)[:10]} if d else None}


def url(u):
    return {"url": u or None}


def checkbox(b):
    return {"checkbox": bool(b)}


def relation(ids):
    return {"relation": [{"id": i} for i in ids]}


def same(current, new_prop) -> bool:
    """True when writing new_prop would not change the value we read as `current`."""
    t, v = next(iter(new_prop.items()))
    if t in ("title", "rich_text"):
        return (current or "") == "".join(x["text"]["content"] for x in v)
    if t == "select":
        return (current or None) == (v["name"] if v else None)
    if t == "multi_select":
        return sorted(current or []) == sorted(o["name"] for o in v)
    if t == "date":
        return (str(current)[:10] if current else None) == (v["start"] if v else None)
    if t == "relation":
        norm_ids = lambda ids: sorted(i.replace("-", "") for i in ids)
        return norm_ids(current or []) == norm_ids(x["id"] for x in v)
    if t == "number":
        return current == v or (current is not None and v is not None and float(current) == float(v))
    if t == "url":
        return (current or None) == (v or None)
    if t == "checkbox":
        return bool(current) == bool(v)
    return False


def changes(current_row: dict, props: dict) -> dict:
    return {k: v for k, v in props.items() if not same(current_row.get(k), v)}
