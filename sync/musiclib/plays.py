"""Reading plays (Spotify export, Last.fm scrobbles) and the ledger that keeps per-year counts.

The ledger is keyed by the raw credit as it appears in the data (normalised), not by Notion row, so
alias changes made later in Notion still roll plays up correctly on the next run. Besides counts it
keeps, per credit, every song's plays and its most intense 30 days ("on repeat"), and per album how
often it was played start to finish."""
import bisect
import gzip
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .text import clean_title, norm

MIN_MS = 30_000


@dataclass
class Play:
    start: datetime          # UTC
    end: datetime | None     # UTC, export only
    artist: str
    album: str
    track: str
    source: str              # "export" or "lastfm"
    shuffle: bool | None = None   # export only; Last.fm doesn't say
    uri: str = ""                 # spotify:track:..., export only


def read_export(paths):
    """Plays from Streaming_History_Audio_*.json files, with counts of what was skipped."""
    plays, stats = [], {"rows": 0, "short": 0, "podcast": 0, "no_track": 0, "files": 0}
    for p in sorted(paths):
        stats["files"] += 1
        for r in json.loads(Path(p).read_text(encoding="utf-8")):
            stats["rows"] += 1
            if r.get("episode_name") or r.get("audiobook_title") or r.get("episode_show_name") or r.get("audiobook_uri"):
                stats["podcast"] += 1
                continue
            uri = r.get("spotify_track_uri") or ""
            if not uri.startswith("spotify:track:") or not r.get("master_metadata_album_artist_name"):
                stats["no_track"] += 1
                continue
            ms = int(r.get("ms_played") or 0)
            if ms < MIN_MS:
                stats["short"] += 1
                continue
            end = datetime.fromisoformat(r["ts"].replace("Z", "+00:00"))
            plays.append(Play(end - timedelta(milliseconds=ms), end, r["master_metadata_album_artist_name"],
                              r.get("master_metadata_album_album_name") or "", r.get("master_metadata_track_name") or "",
                              "export", bool(r.get("shuffle")), uri))
    plays.sort(key=lambda p: p.start)
    stats["plays"] = len(plays)
    return plays, stats


def from_scrobbles(scrobbles):
    return [Play(datetime.fromtimestamp(s["uts"], timezone.utc), None, s["artist"], s["album"], s["track"], "lastfm")
            for s in scrobbles if s.get("artist") and s.get("track")]


def merge(export, scrobbles, window: int = 60):
    """Add scrobbles to export plays, dropping a scrobble when the export already has the same track
    starting (or ending) within `window` seconds. Returns (merged plays, number of duplicates)."""
    times = {}
    for p in export:
        k = norm(p.track)
        times.setdefault(k, []).append(p.start.timestamp())
        if p.end:
            times[k].append(p.end.timestamp())
    for v in times.values():
        v.sort()
    out, dupes = list(export), 0
    for s in scrobbles:
        ts, arr = s.start.timestamp(), times.get(norm(s.track), [])
        i = bisect.bisect_left(arr, ts - window)
        if i < len(arr) and arr[i] <= ts + window:
            dupes += 1
            continue
        out.append(s)
    out.sort(key=lambda p: p.start)
    return out, dupes


def track_id(track: str) -> str:
    return hashlib.sha1(norm(track).encode()).hexdigest()[:8]


def album_key(credit: str, title: str) -> str:
    return norm(credit) + "\t" + norm(clean_title(title))


REPEAT_DAYS = 30          # "on repeat" = most plays of one song within this many days
RUN_GAP = 20 * 60         # seconds between track starts that still count as one listening session
FULL_MIN_TRACKS = 6       # a start-to-finish listen: this many album tracks in a row, not shuffled...
FULL_SHORT_TRACKS = 4     # ...or at least this many when that covers 80% of the album's known tracks


class Ledger:
    """All-time per-year play counts for every credit and album seen, plus the Last.fm cursor.
    Plays must be added oldest first (on-repeat windows and start-to-finish runs depend on order)."""

    def __init__(self, tz: str = "UTC"):
        self.tz = ZoneInfo(tz)
        self.credits: dict[str, dict] = {}
        self.albums: dict[str, dict] = {}
        self.cursor_uts = 0
        self.updated = ""
        self.run = {"k": "", "t": [], "last": 0, "sh": False}   # the album session in progress

    def _song(self, credit, play, local):
        tid = track_id(play.track)
        s = credit.setdefault("songs", {}).setdefault(tid, {"n": play.track, "a": "", "c": 0, "y": {}, "r": 0, "rm": "", "w": []})
        if not s["a"] and play.album.strip():
            s["a"] = clean_title(play.album)
        if play.uri and not s.get("u"):
            s["u"] = play.uri.rsplit(":", 1)[-1]
        s["c"] += 1
        year = str(local.year)
        s["y"][year] = s["y"].get(year, 0) + 1
        day = local.date().toordinal()
        s["w"] = [d for d in s["w"] if d > day - REPEAT_DAYS] + [day]
        if len(s["w"]) > s["r"]:
            s["r"], s["rm"] = len(s["w"]), local.strftime("%Y-%m")

    @staticmethod
    def _keep_uri(entry, play, n):
        """A few Spotify track IDs per credit and album: enough to look up the exact artist and album."""
        if play.uri:
            ids = entry.setdefault("u", [])
            tid = play.uri.rsplit(":", 1)[-1]
            if len(ids) < n and tid not in ids:
                ids.append(tid)

    def _close_run(self):
        run = self.run
        a = self.albums.get(run["k"])
        if a and not run["sh"]:
            n = len(run["t"])
            if n >= FULL_MIN_TRACKS or (n >= FULL_SHORT_TRACKS and n >= 0.8 * len(a["tracks"])):
                a["full"] = a.get("full", 0) + 1
                a["fl"] = run.get("day", "")
        self.run = {"k": "", "t": [], "last": 0, "sh": False}

    def _session(self, play, k, day):
        ts, tid, run = play.start.timestamp(), track_id(play.track), self.run
        if k and k == run["k"] and ts - run["last"] <= RUN_GAP and tid not in run["t"]:
            run["t"].append(tid)
        else:
            self._close_run()
            self.run = run = {"k": k, "t": [tid] if k else [], "last": 0, "sh": False}
        run["last"], run["day"] = ts, day
        run["sh"] = run["sh"] or bool(play.shuffle)

    def _bump(self, entry, play, day, year):
        entry["years"][year] = entry["years"].get(year, 0) + 1
        entry["first"] = min(entry["first"], day) if entry["first"] else day
        entry["last"] = max(entry["last"], day) if entry["last"] else day
        entry["tracks"].add(track_id(play.track))
        entry["src"].add(play.source)

    def add(self, play: Play):
        local = play.start.astimezone(self.tz)
        day, year = local.date().isoformat(), str(local.year)
        c = self.credits.setdefault(norm(play.artist), {"name": play.artist, "years": {}, "first": "", "last": "",
                                                         "tracks": set(), "src": set()})
        self._bump(c, play, day, year)
        month = day[:7]
        mo = c.setdefault("mo", {})   # plays per month, for "This month, years ago"
        mo[month] = mo.get(month, 0) + 1
        self._keep_uri(c, play, 3)
        self._song(c, play, local)
        k = album_key(play.artist, play.album) if play.album.strip() else ""
        if k:
            a = self.albums.setdefault(k, {"artist": play.artist, "title": clean_title(play.album), "variants": [],
                                           "years": {}, "first": "", "last": "", "tracks": set(), "src": set()})
            if play.album not in a["variants"]:
                a["variants"].append(play.album)
            self._bump(a, play, day, year)
            self._keep_uri(a, play, 2)
        self._session(play, k, day)

    # ---- persistence ----
    def save(self, path):
        """Plain JSON with one line per credit and album, so git stores each week's change as a
        small diff (a .gz path still writes the old compressed format)."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.updated = datetime.now(timezone.utc).isoformat(timespec="seconds")
        dump = lambda o: json.dumps(o, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=sorted)
        section = lambda d: ",\n".join(f"{dump(k)}:{dump(v)}" for k, v in sorted(d.items()))
        head = dump({"version": 2, "updated": self.updated, "cursor_uts": self.cursor_uts, "run": self.run})[:-1]
        text = f'{head},"credits":{{\n{section(self.credits)}\n}},"albums":{{\n{section(self.albums)}\n}}}}\n'
        if path.suffix == ".gz":
            with gzip.GzipFile(path, "wb", mtime=0) as f:
                f.write(text.encode())
        else:
            path.write_text(text, encoding="utf-8")
            path.with_name(path.name + ".gz").unlink(missing_ok=True)   # the old compressed copy

    @classmethod
    def load(cls, path, tz: str = "UTC"):
        led = cls(tz)
        path = Path(path)
        legacy = path.with_name(path.name + ".gz")
        if not path.exists() and legacy.exists():   # ledgers written before the switch to plain JSON
            path = legacy
        if path.exists():
            if path.suffix == ".gz":
                with gzip.open(path, "rb") as f:
                    d = json.loads(f.read())
            else:
                d = json.loads(path.read_text(encoding="utf-8"))
            led.credits, led.albums = d.get("credits", {}), d.get("albums", {})
            for e in (*led.credits.values(), *led.albums.values()):
                e["tracks"], e["src"] = set(e["tracks"]), set(e["src"])
            led.cursor_uts, led.updated = int(d.get("cursor_uts") or 0), d.get("updated", "")
            led.run = d.get("run") or led.run
        return led
