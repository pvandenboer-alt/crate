"""The scrobble archive and the Crate report."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from musiclib import report as R  # noqa: E402
from musiclib import scrobbles as S  # noqa: E402
from musiclib.plays import Play  # noqa: E402

NOW = datetime(2026, 12, 21, 20, 0, tzinfo=timezone.utc)
ARTISTS = [{"id": "aaaa1111", "Name": "Postmen", "Aliases": "Postman"},
           {"id": "bbbb2222", "Name": "Sticks"},
           {"id": "cccc3333", "Name": "Opgezwolle"}]
ALBUMS = [{"id": "alb-0001", "Name": "Documenten", "Artist": ["aaaa1111"]}]


def ts(d):
    return int(d.timestamp())


def play(t, artist, track="Track"):
    return Play(t, None, artist, "", track, "lastfm")


def test_archive_keeps_each_scrobble_once(tmp_path):
    path = tmp_path / "scrobbles.jsonl"
    t = NOW - timedelta(days=2)
    rows, new = S.merged(path, [play(t, "Sticks", "A"), play(t + timedelta(minutes=4), "Sticks", "B")])
    assert new == 2
    S.save(path, rows)
    rows, new = S.merged(path, [play(t, "Sticks", "A"), play(t - timedelta(days=1), "Postmen", "C")])
    assert new == 1 and [r[3] for r in rows] == ["C", "A", "B"]   # oldest first
    S.save(path, rows)
    assert len(S.load(path)) == 3


def test_read_events_skips_bad_rows():
    rows = [{"Data": json.dumps([{"t": 1, "e": "play"}, {"e": "no time"}, "junk"])}, {"Data": "{not json"}]
    events, bad = R.read_events(rows)
    assert len(events) == 1 and bad == 1


def scenario():
    """Crate in use from 1 Nov: a Rediscover Play of Postmen (quiet since 2024) that was heard and stuck,
    an album Play from Today's Pick that was heard, and a Search Play that never got listened to."""
    t1 = NOW - timedelta(days=50)          # 1 Nov
    t2 = NOW - timedelta(days=10)
    events = [
        {"t": ts(t1) - 60, "e": "seen", "s": "rediscover", "i": "aaaa1111,cccc3333"},
        {"t": ts(t1) - 30, "e": "open", "s": "rediscover", "r": "rediscover", "k": "artist", "i": "aaaa1111"},
        {"t": ts(t1), "e": "play", "s": "artist", "r": "rediscover", "k": "artist", "i": "aaaa1111", "q": 1},
        {"t": ts(t1) + 5, "e": "play", "s": "artist", "r": "rediscover", "k": "artist", "i": "aaaa1111", "q": 1},  # same day: once
        {"t": ts(t2), "e": "play", "s": "pick", "r": "pick", "k": "album", "i": "alb-0001", "q": 1},
        {"t": ts(t2) + 100, "e": "play", "s": "search", "r": "search", "k": "artist", "i": "bbbb2222"},
    ]
    scrob = [[ts(t1) + 600, "Postman", "", "x"], [ts(t1) + 20 * 86400, "Postmen", "", "y"],
             [ts(t2) + 900, "Postmen", "Documenten", "z"]]
    # Ledger months: Postmen big in 2023, quiet through 2024-25, back in Nov 2026 (and Dec).
    credits = {"postmen": {"name": "Postmen", "mo": {"2023-05": 30, "2026-11": 2, "2026-12": 1}},
               "sticks": {"name": "Sticks", "mo": {"2024-01": 25, "2026-02": 1, "2026-03": 3}},
               "opgezwolle": {"name": "Opgezwolle", "mo": {"2022-01": 40, "2026-06": 2}}}
    return events, scrob, credits


def test_funnel_per_feature_and_start():
    events, scrob, credits = scenario()
    parts = R.build(events, scrob, ARTISTS, ALBUMS, credits, NOW)
    tables = {p[1][0]: p[2] for p in parts if p[0] == "table"}
    feats = {r[0]: r[1:] for r in tables["Feature"]}
    # Seen, Opened, Played, Heard, Rediscovered, Stuck
    assert feats["Rediscover"] == ["2", "1", "–", "–", "–", "–"]
    assert feats["Artist page"] == ["–", "–", "1", "1", "1", "1 of 1"]   # stuck: more Postmen 20 days later
    assert feats["Today's Pick"] == ["–", "–", "1", "1", "1", "–"]       # too recent to tell
    assert feats["Search"] == ["–", "–", "1", "–", "–", "–"]             # never heard
    starts = {r[0]: r[1:] for r in tables["Started from"]}
    assert starts["Rediscover"] == ["2", "1", "1", "1", "1", "1 of 1"]   # the artist-page Play counts for its root
    md = R.markdown(parts)
    assert "Back from the quiet" in md and "Postmen – Documenten: Today's Pick" in md


def test_monthly_rediscoveries_against_the_baseline():
    events, scrob, credits = scenario()
    parts = R.build(events, scrob, ARTISTS, ALBUMS, credits, NOW)
    months = {r[0]: r[1:] for r in next(p[2] for p in parts if p[0] == "table" and p[1][0] == "Month")}
    assert months["Nov 2026"] == ["1", "1", "–"]           # Postmen, through Crate; too early to say whether it stuck
    assert months["Dec 2026 (so far)"] == ["0", "0", "–"]
    base = next(p[1] for p in parts if p[0] == "p" and p[1].startswith("Artists back"))
    assert "Before Crate: 0.2 a month" in base             # Sticks (Feb) and Opgezwolle (Jun): 2 in the 12 months before Nov
    assert "50% of them stuck" in base                     # Sticks played again in March; Opgezwolle not


def test_no_events_yet():
    parts = R.build([], [], ARTISTS, ALBUMS, {}, NOW)
    assert any(p[0] == "p" and p[1].startswith("No Crate usage logged yet") for p in parts)


class PageNotion:
    def __init__(self, existing):
        self.blocks, self.calls = list(existing), []

    def request(self, method, path, body=None):
        self.calls.append((method, path))
        if method == "GET":
            return {"results": self.blocks, "has_more": False}
        if method == "DELETE":
            self.blocks = [b for b in self.blocks if b["id"] != path.rsplit("/", 1)[1]]
            return {}
        self.blocks += body["children"]
        return {}


def test_write_page_replaces_content_but_keeps_sub_pages():
    n = PageNotion([{"id": "old1", "type": "paragraph"}, {"id": "db", "type": "child_database"}])
    events, scrob, credits = scenario()
    new = R.blocks(R.build(events, scrob, ARTISTS, ALBUMS, credits, NOW))
    R.write_page(n, "page", new)
    assert ("DELETE", "/blocks/old1") in n.calls and ("DELETE", "/blocks/db") not in n.calls
    tables = [b for b in n.blocks if b.get("type") == "table"]
    assert tables and all(len(r["table_row"]["cells"]) == t["table"]["table_width"] for t in tables for r in t["table"]["children"])


def test_stats_for_crate_round_trip_through_the_app_data_block():
    events, scrob, credits = scenario()
    data = R.stats(events, scrob, ARTISTS, ALBUMS, credits, NOW)
    assert [m["m"] for m in data["months"]][0] == "2026-05" and data["months"][-1] == {
        "m": "2026-12", "n": 0, "via": 0, "stuck": 0, "ripe": False, "crate": True, "partial": True}
    assert [m["crate"] for m in data["months"]].count(False) == R.CONTEXT_MONTHS   # context before Crate
    assert data["baseline"] == {"per_month": 0.2, "stick": 0.5}
    assert data["usage"]["played"] == 3 and data["features"][0]["k"] in ("artist", "rediscover")
    assert data["back"][0]["album"] == "Documenten" and data["back"][0]["s"] == "pick"
    big = {**data, "pad": "é" * 5000}   # several 2000-character pieces, non-ASCII included
    block = R.app_data_block(big)
    assert block["heading_3"]["is_toggleable"] and block["heading_3"]["rich_text"][0]["text"]["content"] == "App data"
    code = block["heading_3"]["children"][0]["code"]["rich_text"]
    assert len(code) > 1 and all(len(p["text"]["content"]) <= 2000 for p in code)
    assert json.loads("".join(p["text"]["content"] for p in code)) == big


def test_through_crate_only_counts_the_comeback_play_itself():
    """A comeback before Crate (or one whose first play wasn't after a Crate Play) is never "through Crate",
    even when a later scrobble of that artist did follow a Crate Play."""
    events, scrob, credits = scenario()
    credits["opgezwolle"]["mo"]["2026-12"] = 1
    later = NOW - timedelta(days=3)
    events.append({"t": ts(later), "e": "play", "s": "search", "k": "artist", "i": "cccc3333"})
    scrob.append([ts(later) + 60, "Opgezwolle", "", "w"])
    months = {m["m"]: m for m in R.stats(events, sorted(scrob), ARTISTS, ALBUMS, credits, NOW)["months"]}
    assert months["2026-06"]["n"] == 1 and months["2026-06"]["via"] == 0
