"""Set up Notion for Crate: find or create the databases and the report page, and record their IDs.

    python sync/setup.py                      # use the one page your integration can see
    python sync/setup.py --page <page link>   # or name the page to create everything in
    python sync/setup.py --dry-run            # only report what it would do

Safe to run again: databases that already exist (recognised by their columns, so renaming them in Notion
is fine) are reused, missing columns are added, and nothing is deleted.
"""
import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from musiclib import notion as N  # noqa: E402
from musiclib.pipeline import summary  # noqa: E402

CONFIG = Path(__file__).resolve().parent / "config.yml"
ORDER = ["artists", "albums", "new_releases", "blocklist", "crate_log"]   # relations need their target first
KEY_IN_CONFIG = {"artists": "artists", "albums": "albums", "new_releases": "new_releases", "blocklist": "blocklist",
                 "crate_log": "crate_log"}
ID_RE = re.compile(r"([0-9a-f]{8})-?([0-9a-f]{4})-?([0-9a-f]{4})-?([0-9a-f]{4})-?([0-9a-f]{12})", re.I)


class SetupError(RuntimeError):
    pass


def page_id(text: str) -> str:
    """The page ID in a Notion link (or an ID on its own)."""
    m = list(ID_RE.finditer(text or ""))
    if not m:
        raise SetupError(f"'{text}' doesn't look like a Notion page link or ID.")
    return "-".join(m[-1].groups()).lower()


def search(notion, kind: str, query: str = ""):
    out, cursor = [], None
    while True:
        body = {"filter": {"property": "object", "value": kind}, "page_size": 100}
        if query:
            body["query"] = query
        if cursor:
            body["start_cursor"] = cursor
        d = notion.request("POST", "/search", body)
        out += [x for x in d.get("results", []) if not x.get("in_trash") and not x.get("archived")]
        if not d.get("has_more") or len(out) > 1000:
            return out
        cursor = d.get("next_cursor")


def title_of(obj) -> str:
    if obj.get("object") == "data_source" or "title" in obj and isinstance(obj["title"], list):
        return "".join(t.get("plain_text", "") for t in obj.get("title") or [])
    for p in (obj.get("properties") or {}).values():
        if p.get("type") == "title":
            return "".join(t.get("plain_text", "") for t in p.get("title") or [])
    return ""


def find_existing(notion):
    """Data sources the integration can see that look like Crate's, keyed by database. More than one
    candidate for the same database is a problem the person has to sort out (we never guess)."""
    found = {}
    for ds in search(notion, "data_source"):
        props = ds.get("properties") or notion.data_source(ds["id"]).get("properties") or {}
        for key in N.DATABASES:
            if N.looks_like(props, key):
                found.setdefault(key, []).append(ds)
    return found


def parent_page(notion, wanted: str):
    if wanted:
        pid = page_id(wanted)
        notion.request("GET", f"/pages/{pid}")   # fails clearly when the integration can't see it
        return pid
    pages = [p for p in search(notion, "page") if (p.get("parent") or {}).get("type") in ("workspace", "page_id")]
    tops = [p for p in pages if not any((p.get("parent") or {}).get("page_id", "").replace("-", "") == q["id"].replace("-", "") for q in pages)]
    if len(tops) == 1:
        return tops[0]["id"]
    if not tops:
        raise SetupError("Your Notion integration can't see any page. In Notion, make a page (for example "
                         "'Music Library'), open ••• → Connections and add your integration, then run this again.")
    names = ", ".join(f"'{title_of(p) or 'Untitled'}'" for p in tops[:10])
    raise SetupError(f"Your integration can see several pages ({names}). Run this again with the link of the page "
                     "Crate should use (the workflow's 'page' box).")


def create_database(notion, parent: str, key: str, ids: dict) -> str:
    spec = N.DATABASES[key]
    props = {}
    for name, p in spec["props"].items():
        if p[0] == "relation" and (len(p) < 3 or p[1] == key):
            continue   # the other side creates it (a two-way relation), or it points at itself: added below
        props[name] = N.property_body(p, ids)
    d = notion.request("POST", "/databases", {
        "parent": {"type": "page_id", "page_id": parent}, "title": [{"type": "text", "text": {"content": spec["title"]}}],
        "icon": {"type": "emoji", "emoji": spec["icon"]}, "initial_data_source": {"properties": props}})
    sources = d.get("data_sources") or []
    if not sources:
        raise SetupError(f"Notion created {spec['title']} but returned no data source.")
    ids[key] = sources[0]["id"]
    self_rel = {n: N.property_body(p, ids) for n, p in spec["props"].items() if p[0] == "relation" and p[1] == key}
    if self_rel:
        notion.request("PATCH", f"/data_sources/{ids[key]}", {"properties": self_rel})
    for p in spec["props"].values():
        if p[0] == "relation" and len(p) > 2:
            name_synced_side(notion, ids[p[1]], ids[key], p[2])
    return ids[key]


def name_synced_side(notion, ds_id: str, points_at: str, wanted: str):
    """A two-way relation also appears on the other database, under a name Notion picks ("Related to
    Albums (Artist)"). Give it the name the sync expects."""
    props = notion.data_source(ds_id).get("properties") or {}
    if wanted in props:
        return
    clean = lambda x: str(x or "").replace("-", "")
    for name, p in props.items():
        if p.get("type") == "relation" and clean((p.get("relation") or {}).get("data_source_id")) == clean(points_at):
            notion.request("PATCH", f"/data_sources/{ds_id}", {"properties": {p.get("id") or name: {"name": wanted}}})
            return


def add_missing_columns(notion, key: str, ds_id: str, ids: dict) -> list:
    """Columns an older or hand-made database lacks (two-way relations excepted: the other side adds those)."""
    have = notion.data_source(ds_id).get("properties") or {}
    missing = {n: p for n, p in N.DATABASES[key]["props"].items()
               if n not in have and p[0] != "title" and not (p[0] == "relation" and len(p) < 3 and p[1] != key)}
    body = {}
    for n, p in missing.items():
        if p[0] == "relation" and p[1] not in ids:
            continue
        body[n] = N.property_body(p, ids)
    if body:
        notion.request("PATCH", f"/data_sources/{ds_id}", {"properties": body})
    return sorted(body)


def find_report(notion) -> str:
    for p in search(notion, "page", N.REPORT_TITLE):
        if title_of(p).strip().lower() == N.REPORT_TITLE.lower():
            return p["id"]
    return ""


def create_report(notion, parent: str) -> str:
    p = notion.request("POST", "/pages", {
        "parent": {"type": "page_id", "page_id": parent}, "icon": {"type": "emoji", "emoji": "📊"},
        "properties": {"title": {"title": [{"type": "text", "text": {"content": N.REPORT_TITLE}}]}},
        "children": [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {
            "content": "The weekly sync rewrites this page with the Crate report. Crate's Stats screen reads it."}}]}}]})
    return p["id"]


def write_config(ids: dict, path: Path = CONFIG):
    """Put the IDs into config.yml's notion: block, keeping everything else (comments included)."""
    text = path.read_text(encoding="utf-8")
    for key, val in ids.items():
        text, n = re.subn(rf"^(  {re.escape(key)}:)[^\n#]*", rf'\g<1> "{val}"', text, count=1, flags=re.M)
        if not n:
            raise SetupError(f"config.yml has no 'notion: {key}:' line to fill in.")
    path.write_text(text, encoding="utf-8")


def setup(notion, page: str = "", dry: bool = False, config: Path = CONFIG) -> dict:
    found = find_existing(notion)
    clash = {k: v for k, v in found.items() if len(v) > 1}
    if clash:
        lines = "; ".join(f"{N.DATABASES[k]['title']}: {len(v)}" for k, v in clash.items())
        raise SetupError(f"Found more than one database of a kind ({lines}). Remove the integration from the copies "
                         "Crate shouldn't use (••• → Connections), then run this again.")
    ids = {k: v[0]["id"] for k, v in found.items()}
    missing = [k for k in ORDER if k not in ids]
    rep = find_report(notion)
    parent = parent_page(notion, page) if (missing or not rep) else ""
    report = []
    for key in ORDER:
        title = N.DATABASES[key]["title"]
        if key in ids:
            added = [] if dry else add_missing_columns(notion, key, ids[key], ids)
            report.append(f"{title}: found" + (f", added {', '.join(added)}" if added else ""))
        elif dry:
            report.append(f"{title}: would be created")
        else:
            create_database(notion, parent, key, ids)
            report.append(f"{title}: created")
    if rep:
        report.append(f"{N.REPORT_TITLE}: found")
    elif dry:
        report.append(f"{N.REPORT_TITLE}: would be created")
    else:
        rep = create_report(notion, parent)
        report.append(f"{N.REPORT_TITLE}: created")
    if not dry:
        write_config({KEY_IN_CONFIG[k]: v for k, v in ids.items()} | {"crate_report": rep}, config)
    return {"ids": ids, "report": rep, "lines": report}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", default=os.environ.get("NOTION_PAGE", ""))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    try:
        out = setup(N.Notion(os.environ.get("NOTION_TOKEN", "")), args.page.strip(), args.dry_run)
    except (SetupError, N.NotionError) as e:
        summary(f"## Set up Notion\n\n**Not done:** {e}\n")
        sys.exit(str(e))
    summary("## Set up Notion\n\n" + "\n".join(f"- {line}" for line in out["lines"]) + "\n\n"
            + ("Nothing was changed (dry run).\n" if args.dry_run else
               "The database IDs are saved in `sync/config.yml`. Next: run **Import Spotify history** (dry run first).\n"))


if __name__ == "__main__":
    main()
