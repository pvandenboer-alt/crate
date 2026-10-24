"""Set up Notion (setup.py) against a fake Notion that understands search, database creation and schema
updates."""
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import setup as S  # noqa: E402
from musiclib import notion as N  # noqa: E402

CONFIG = """notion:
  artists: ""
  albums: ""
  new_releases: ""
  blocklist: ""
  crate_log: ""          # written by Crate
  crate_report: ""       # a page
lastfm_user: ""
"""


class SetupNotion:
    def __init__(self, pages=("Music Library",), honour_synced_names=True):
        self.honour = honour_synced_names
        self.pages = {str(uuid.uuid4()): {"title": t, "parent": {"type": "workspace"}} for t in pages}
        self.sources = {}   # id -> {"title", "properties"}
        self.targets = {}   # (data source, property) -> data source a relation points at
        self.calls = []

    def _prop_type(self, body):
        return next(iter(body))

    def request(self, method, path, body=None):
        self.calls.append((method, path))
        if path == "/search":
            kind = body["filter"]["value"]
            if kind == "data_source":
                res = [{"object": "data_source", "id": i, "title": [{"plain_text": d["title"]}],
                        "properties": {n: {"type": t} for n, t in d["properties"].items()}} for i, d in self.sources.items()]
            else:
                res = [{"object": "page", "id": i, "parent": p["parent"],
                        "properties": {"title": {"type": "title", "title": [{"plain_text": p["title"]}]}}}
                       for i, p in self.pages.items() if body.get("query", "").lower() in p["title"].lower()]
            return {"results": res, "has_more": False}
        if method == "POST" and path == "/databases":
            ds = str(uuid.uuid4())
            props = {n: self._prop_type(b) for n, b in body["initial_data_source"]["properties"].items()}
            self.sources[ds] = {"title": body["title"][0]["text"]["content"], "properties": props}
            for n, b in body["initial_data_source"]["properties"].items():
                rel = b.get("relation")
                if rel:
                    self.targets[(ds, n)] = rel["data_source_id"]
                if rel and rel.get("type") == "dual_property":   # the synced side appears on the target
                    name = rel["dual_property"]["synced_property_name"] if self.honour else f"Related to {self.sources[ds]['title']} ({n})"
                    self.sources[rel["data_source_id"]]["properties"][name] = "relation"
                    self.targets[(rel["data_source_id"], name)] = ds
            return {"id": str(uuid.uuid4()), "data_sources": [{"id": ds}]}
        if method == "PATCH" and path.startswith("/data_sources/"):
            ds = path.split("/")[2]
            for n, b in body["properties"].items():
                if set(b) == {"name"}:   # a rename
                    self.sources[ds]["properties"][b["name"]] = self.sources[ds]["properties"].pop(n)
                    self.targets[(ds, b["name"])] = self.targets.pop((ds, n))
                    continue
                self.sources[ds]["properties"][n] = self._prop_type(b)
                if "relation" in b:
                    self.targets[(ds, n)] = b["relation"]["data_source_id"]
            return {}
        if method == "GET" and path.startswith("/data_sources/"):
            ds = path.split("/")[2]
            d = self.sources[ds]
            return {"properties": {n: {"type": t, "id": n, **({"relation": {"data_source_id": self.targets[(ds, n)]}} if t == "relation" else {})}
                                   for n, t in d["properties"].items()}}
        if method == "GET" and path.startswith("/pages/"):
            pid = path.split("/")[2]
            if pid not in self.pages:
                raise N.NotionError("404 object_not_found")
            return {"id": pid}
        if method == "POST" and path == "/pages":
            pid = str(uuid.uuid4())
            self.pages[pid] = {"title": body["properties"]["title"]["title"][0]["text"]["content"],
                               "parent": {"type": "page_id", "page_id": body["parent"]["page_id"]}}
            return {"id": pid}
        raise NotImplementedError((method, path))

    data_source = N.Notion.data_source

    def validate(self, ids):
        return N.Notion.validate(self, ids)


def test_setup_creates_everything_and_fills_in_config(tmp_path):
    cfg = tmp_path / "config.yml"
    cfg.write_text(CONFIG)
    n = SetupNotion()
    out = S.setup(n, config=cfg)
    assert set(out["ids"]) == {"artists", "albums", "new_releases", "blocklist", "crate_log"} and out["report"]
    n.validate({k: out["ids"][k] for k in N.SCHEMA})   # every property the sync uses, with the right type
    art = n.sources[out["ids"]["artists"]]["properties"]
    assert art["Similar artists"] == art["Albums"] == art["New releases"] == "relation"
    text = cfg.read_text()
    assert f'artists: "{out["ids"]["artists"]}"' in text and "# written by Crate" in text and f'crate_report: "{out["report"]}"' in text
    # Running it again finds everything and creates nothing.
    before = len(n.sources)
    again = S.setup(n, config=cfg)
    assert len(n.sources) == before and again["ids"] == out["ids"] and all("found" in line for line in again["lines"])


def test_setup_adds_missing_columns_to_an_existing_database(tmp_path):
    cfg = tmp_path / "config.yml"
    cfg.write_text(CONFIG)
    n = SetupNotion()
    S.setup(n, config=cfg)
    ds = next(i for i, d in n.sources.items() if d["title"] == "New Releases")
    del n.sources[ds]["properties"]["Deezer ID"]
    out = S.setup(n, config=cfg)
    assert "Deezer ID" in n.sources[ds]["properties"] and any("added Deezer ID" in line for line in out["lines"])


def test_setup_needs_a_page_and_never_guesses(tmp_path):
    cfg = tmp_path / "config.yml"
    cfg.write_text(CONFIG)
    with pytest.raises(S.SetupError, match="can't see any page"):
        S.setup(SetupNotion(pages=()), config=cfg)
    two = SetupNotion(pages=("Music", "Recipes"))
    with pytest.raises(S.SetupError, match="several pages"):
        S.setup(two, config=cfg)
    page = next(i for i, p in two.pages.items() if p["title"] == "Music")
    out = S.setup(two, page=f"https://www.notion.so/Music-{page.replace('-', '')}", config=cfg)
    assert len(out["ids"]) == 5
    # A second Crate database of the same kind (a duplicate someone shared) stops it.
    S.create_database(two, page, "albums", dict(out["ids"]))
    with pytest.raises(S.SetupError, match="more than one"):
        S.setup(two, config=cfg)


def test_dry_run_changes_nothing(tmp_path):
    cfg = tmp_path / "config.yml"
    cfg.write_text(CONFIG)
    n = SetupNotion()
    out = S.setup(n, dry=True, config=cfg)
    assert not n.sources and cfg.read_text() == CONFIG and all("would be created" in line for line in out["lines"])


def test_markers_tell_the_databases_apart():
    for key, spec in N.DATABASES.items():
        props = {n: {"type": p[0]} for n, p in spec["props"].items()}
        assert [k for k in N.DATABASES if N.looks_like(props, k)] == [key]


def test_sync_asks_for_setup_when_config_has_no_ids():
    from musiclib.pipeline import Run
    run = Run({"notion": {"artists": "", "albums": "", "new_releases": "", "blocklist": ""}}, notion=None)
    with pytest.raises(N.NotionError, match="Set up Notion"):
        run.load()


def test_scrobbles_start_right_after_the_export():
    from datetime import datetime, timezone
    from importer import scrobbles_start
    from musiclib.plays import Play
    last = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
    export = [Play(datetime(2020, 1, 1, tzinfo=timezone.utc), None, "A", "B", "C", "export"),
              Play(last, None, "A", "B", "D", "export")]
    assert scrobbles_start({"scrobbles_from": ""}, export) == last
    assert scrobbles_start({"scrobbles_from": "2026-10-19T00:00:00+00:00"}, export).day == 19


def test_slim_export_keeps_only_what_the_importer_needs(tmp_path):
    import json
    from slim_export import slim
    from musiclib.plays import read_export
    src, dst = tmp_path / "in", tmp_path / "out"
    src.mkdir()
    rows = [
        {"ts": "2020-01-01T10:00:00Z", "ms_played": 200000, "ip_addr": "203.0.113.9", "platform": "android", "conn_country": "NL",
         "master_metadata_track_name": "Song", "master_metadata_album_artist_name": "Artist", "master_metadata_album_album_name": "Album",
         "spotify_track_uri": "spotify:track:1234567890abcdefghij", "reason_start": "trackdone", "reason_end": "trackdone", "shuffle": False, "skipped": False},
        {"ts": "2020-01-01T11:00:00Z", "ms_played": 900000, "ip_addr": "203.0.113.9", "episode_name": "A podcast", "episode_show_name": "Show"},
    ]
    (src / "Streaming_History_Audio_2020_0.json").write_text(json.dumps(rows))
    t = slim(src, dst)
    out = json.loads((dst / "Streaming_History_Audio_2020_0.json").read_text())
    assert t == {"files": 1, "plays": 1, "dropped": 1}
    assert "ip_addr" not in out[0] and "platform" not in out[0] and "conn_country" not in out[0]
    plays, _ = read_export(sorted(dst.glob("*.json")))
    assert len(plays) == 1 and plays[0].artist == "Artist" and plays[0].uri.endswith("ghij")


def test_import_without_an_export_uses_all_of_last_fm(tmp_path):
    from datetime import datetime, timezone
    from importer import do_import
    from fakes import FakeLastFm
    from test_sync import TAGS, SIMILAR, make_run, seeded
    run = make_run(seeded(), dry=False)
    run.cfg["scrobbles_from"] = ""
    old = datetime(2012, 5, 1, 20, 0, tzinfo=timezone.utc)
    scrob = [{"uts": int(old.timestamp()) + i * 600, "artist": "Old Favourite", "album": "Back Then", "track": f"Song {i % 9}"} for i in range(40)]
    run.lastfm = FakeLastFm(scrob, tags=TAGS, similar=SIMILAR)
    do_import(run, [], ledger_path=tmp_path / "ledger.json")
    names = [r["Name"] for r in run.notion.rows("artists")]
    assert "Old Favourite" in names   # 40 plays in 2012 pass the all-time thresholds


def test_setup_names_the_other_side_of_two_way_relations(tmp_path):
    cfg = tmp_path / "config.yml"
    cfg.write_text(CONFIG)
    n = SetupNotion(honour_synced_names=False)   # Notion picks its own name for the synced side
    out = S.setup(n, config=cfg)
    n.validate({k: out["ids"][k] for k in N.SCHEMA})
    art = n.sources[out["ids"]["artists"]]["properties"]
    assert "Albums" in art and "New releases" in art and not any(k.startswith("Related to") for k in art)
