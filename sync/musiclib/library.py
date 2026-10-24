"""Turns ledger credits into library artists and albums: alias roll-ups, multi-artist credits,
thresholds, levels, peak years, genres and the block list."""
import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field

from .text import clean_title, compact, credit_parts, is_multi_credit, norm, split_aliases

LEVELS = ("A whole lot", "Quite a bit", "A bit")


# ---------- resolving credits to artists ----------
@dataclass
class Resolution:
    key: str            # "page:<notion id>" for a library row, "new:<compact name>" otherwise
    rule: str           # name | alias | multi | new
    display: str        # name to show / create for a new artist


MIN_SOLO_PLAYS = 20


class Resolver:
    """credit_plays maps a normalised credit to its plays, used to decide multi-artist credits."""

    def __init__(self, artist_rows, credit_plays=None):
        self.by_name, self.by_alias, self.rows = {}, {}, {}
        for r in artist_rows:
            self.rows[r["id"]] = r
            self.by_name.setdefault(compact(r.get("Name")), r["id"])
            for a in split_aliases(r.get("Aliases")):
                self.by_alias.setdefault(compact(a), r["id"])
        self.plays = dict(credit_plays or {})

    def _lookup(self, name):
        c = compact(name)
        if c in self.by_name:
            return self.by_name[c], "name"
        if c in self.by_alias:
            return self.by_alias[c], "alias"
        return None, None

    def resolve(self, credit: str) -> Resolution:
        rid, rule = self._lookup(credit)
        if rid:
            return Resolution("page:" + rid, rule, self.rows[rid].get("Name") or credit)
        if is_multi_credit(credit):
            first = credit_parts(credit)[0]
            rid, _ = self._lookup(first)
            if rid:
                return Resolution("page:" + rid, "multi", self.rows[rid].get("Name") or first)
            # Otherwise only when the first artist clearly exists on their own: enough solo plays, and
            # at least as many as the joint credit. Duos and bands ("Simon & Garfunkel",
            # "Hall & Oates") stay whole.
            solo, joint = self.plays.get(norm(first), 0), self.plays.get(norm(credit), 0)
            if solo >= MIN_SOLO_PLAYS and solo >= joint:
                return Resolution("new:" + compact(first), "multi", first)
        return Resolution("new:" + compact(credit), "new", credit)


# ---------- library entities ----------
@dataclass
class Entity:
    key: str
    name: str
    years: Counter = field(default_factory=Counter)
    tracks: set = field(default_factory=set)
    first: str = ""
    last: str = ""
    src: set = field(default_factory=set)
    credits: Counter = field(default_factory=Counter)   # original credit -> plays
    rules: dict = field(default_factory=dict)            # original credit -> resolution rule
    row: dict | None = None
    songs: dict = field(default_factory=dict)            # track id -> song stats (artists)
    months: Counter = field(default_factory=Counter)      # "YYYY-MM" -> plays (artists)
    # albums only
    artist_key: str = ""
    variants: set = field(default_factory=set)
    full: int = 0                                         # times played start to finish
    track_ids: list = field(default_factory=list)         # Spotify track IDs played (exact lookups)

    @property
    def plays(self):
        return sum(self.years.values())

    @property
    def peak_year(self):
        if not self.years:
            return None
        return int(max(self.years.items(), key=lambda kv: (kv[1], kv[0]))[0])

    def absorb(self, entry, credit=None, rule=None):
        self.years.update({k: int(v) for k, v in entry["years"].items()})
        self.tracks |= set(entry["tracks"])
        self.src |= set(entry["src"])
        if entry["first"]:
            self.first = min(self.first, entry["first"]) if self.first else entry["first"]
        if entry["last"]:
            self.last = max(self.last, entry["last"]) if self.last else entry["last"]
        if credit is not None:
            self.credits[credit] += sum(int(v) for v in entry["years"].values())
            self.rules[credit] = rule
        self.full += int(entry.get("full") or 0)
        self.months.update({k: int(v) for k, v in (entry.get("mo") or {}).items()})
        if rule in (None, "name", "alias", "new"):   # a multi-artist credit's tracks may belong to the other artist
            self.track_ids += [t for t in entry.get("u") or [] if t not in self.track_ids][:3]
        for tid, s in (entry.get("songs") or {}).items():
            m = self.songs.get(tid)
            if not m:
                self.songs[tid] = {"n": s["n"], "a": s.get("a", ""), "c": s["c"], "y": dict(s["y"]), "r": s["r"], "rm": s["rm"],
                                   "u": s.get("u", "")}
                continue
            if s.get("u") and not m.get("u"):
                m["u"] = s["u"]
            if s["c"] > m["c"] and s.get("a"):
                m["a"] = s["a"]
            m["c"] += s["c"]
            for y, n in s["y"].items():
                m["y"][y] = m["y"].get(y, 0) + n
            if s["r"] > m["r"]:
                m["r"], m["rm"] = s["r"], s["rm"]

    def months_text(self) -> str:
        """Plays per month, one year per part: '2014:0.0.3.12.0.0.0.0.0.23.4.1; 2015:...' (fits Notion's 2000)."""
        years = sorted({m[:4] for m in self.months})
        parts = []
        for y in years:
            row = [self.months.get(f"{y}-{m:02d}", 0) for m in range(1, 13)]
            if any(row):
                parts.append(y + ":" + ".".join(map(str, row)))
        return "; ".join(parts)

    def top_songs(self, keep=10, repeat_min=12, cap=16, limit=1990) -> str:
        """JSON for Notion's Top songs: [title, album, plays, peak year, best 30-day plays, that month,
        Spotify track ID]. The most played songs, plus any you had on repeat, most played first."""
        if not self.songs:
            return ""
        ranked = sorted(self.songs.values(), key=lambda s: (-s["c"], s["n"]))
        pick = ranked[:keep] + [s for s in ranked[keep:] if s["r"] >= repeat_min]
        rows = [[s["n"], s["a"], s["c"], int(max(s["y"].items(), key=lambda kv: (kv[1], kv[0]))[0]), s["r"], s["rm"], s.get("u", "")]
                for s in sorted(pick[:cap], key=lambda s: -s["c"])]
        while rows:
            out = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
            if len(out) <= limit:
                return out
            rows.pop()
        return ""


def build(ledger, artist_rows, album_rows):
    """Aggregate the ledger into artist and album entities, matched to existing Notion rows."""
    resolver = Resolver(artist_rows, {k: sum(int(v) for v in c["years"].values()) for k, c in ledger.credits.items()})
    artists: dict[str, Entity] = {}
    for n, c in ledger.credits.items():
        res = resolver.resolve(c["name"])
        e = artists.get(res.key)
        if not e:
            e = artists[res.key] = Entity(res.key, res.display)
            if res.key.startswith("page:"):
                e.row = resolver.rows[res.key[5:]]
        e.absorb(c, c["name"], res.rule)
    # New artists are named after their most-played own credit (not a multi-artist one).
    for e in artists.values():
        if not e.row:
            own = [(p, c) for c, p in e.credits.items() if e.rules[c] == "new"]
            if own:
                e.name = max(own)[1]

    # Existing album rows: by artist row + normalised title (and every stored title variant).
    album_index = {}
    for r in album_rows:
        for aid in r.get("Artist") or []:
            for t in [r.get("Name"), *split_aliases(r.get("Title variants"))]:
                album_index.setdefault(("page:" + aid.replace("-", ""), norm(clean_title(t))), r)
    albums: dict[str, Entity] = {}
    for a in ledger.albums.values():
        res = resolver.resolve(a["artist"])
        k = f"{res.key}\t{norm(a['title'])}"
        e = albums.get(k)
        if not e:
            e = albums[k] = Entity(k, a["title"], artist_key=res.key)
            e.row = album_index.get((res.key, norm(a["title"])))
        e.absorb(a, a["artist"], res.rule)
        e.variants |= set(a["variants"])
    return resolver, artists, albums


def credited_as(album: Entity, artist_name: str) -> str:
    """The album's credit when it differs from the artist it rolled up to."""
    if not album.credits:
        return ""
    credit = album.credits.most_common(1)[0][0]
    return "" if norm(credit) == norm(artist_name) else credit


# ---------- thresholds & levels ----------
def artist_passes(e: Entity, t) -> bool:
    return e.plays >= t["artist_plays"] or len(e.tracks) >= t["artist_tracks"]


def album_passes(e: Entity, t) -> bool:
    return len(e.tracks) >= t["album_tracks"] and e.plays >= t["album_plays"]


def levels(items, top: float, mid: float) -> dict:
    """{key: level} for (key, plays) pairs ranked by plays: top share, next share, the rest."""
    ranked = sorted(items, key=lambda kv: (-(kv[1] or 0), str(kv[0])))
    n = len(ranked)
    n_top = math.ceil(n * top) if n else 0
    n_mid = math.ceil(n * (top + mid)) if n else 0
    return {k: LEVELS[0] if i < n_top else LEVELS[1] if i < n_mid else LEVELS[2] for i, (k, _) in enumerate(ranked)}


# ---------- genres, tags and the block list ----------
class GenreMap:
    def __init__(self, mapping: dict):
        mapping = dict(mapping or {})
        ambiguous = mapping.pop("_ambiguous", None) or {}
        keywords = mapping.pop("_keywords", None) or []
        self.tag_to_genre = {}
        for genre, tags in mapping.items():
            for t in tags or []:
                self.tag_to_genre.setdefault(str(t).strip().lower(), genre)
        self.genres = list(mapping.keys())
        # tag -> {genre: set(context tags)}
        self.ambiguous = {str(t).lower(): {g: {str(c).lower() for c in ctx or []} for g, ctx in opts.items()}
                          for t, opts in ambiguous.items()}
        # Fallback rules for tags not listed above, e.g. Spotify's "dutch trap" or a tag "hip hop nl".
        self.keywords = [(re.compile(p, re.I), g) for p, g in (keywords or [])]

    def _resolve(self, tag, weights):
        """Pick the genre for an ambiguous tag from the artist's other tags ('' when unclear)."""
        scores = {g: sum(w for t, w in weights.items() if t in ctx) for g, ctx in self.ambiguous[tag].items()}
        best = max(scores.items(), key=lambda kv: kv[1], default=("", 0))
        return best[0] if best[1] > 0 else ""

    def genres_for(self, tags, min_weight=30, max_genres=3, curated=False):
        """Genres from tags, strongest first. For Last.fm tags, keyword rules only apply when no tag maps
        exactly, so they fill gaps without adding stray extras (a 'liquid funk' tag doesn't make a drum & bass act
        Funk). Spotify's genre names are curated, so with curated=True every one may use the keywords."""
        if curated:
            return self._genres(tags, min_weight, max_genres, None)
        return self._genres(tags, min_weight, max_genres, False) or self._genres(tags, min_weight, max_genres, True)

    def _genres(self, tags, min_weight, max_genres, keywords):
        """keywords: False = exact map only, True = keyword rules only, None = exact, else keywords."""
        out = []
        weights = {n.lower(): w for n, w in tags}
        for name, weight in sorted(tags, key=lambda t: -t[1]):
            if weight < min_weight:
                break
            name = name.lower()
            exact = self.tag_to_genre.get(name) or (self._resolve(name, weights) if name in self.ambiguous else "")
            loose = "" if name in self.ambiguous else next((genre for rx, genre in self.keywords if rx.search(name)), "")
            g = exact if keywords is False else loose if keywords is True else (exact or loose)
            if g and g not in out:
                out.append(g)
            if len(out) >= max_genres:
                break
        return out


def tags_text(tags, keep=20):
    return "; ".join(f"{n}:{w}" for n, w in sorted(tags, key=lambda t: -t[1])[:keep] if w > 0)


class Blocklist:
    """sync/blocklist.yml (artist names, tag patterns) plus the Notion Blocklist database, which the
    Crate app adds to: artists, albums and songs (optionally "by" an artist) and genres."""

    def __init__(self, data: dict, rows=()):
        data = data or {}
        self.names = {norm(n) for n in data.get("artists") or [] if n}
        self.patterns = [re.compile(p, re.I) for p in data.get("tags") or [] if p]
        self.albums, self.songs, self.genres = set(), set(), set()
        self.add_rows(rows)

    def add_rows(self, rows):
        for r in rows:
            name, kind, by = (r.get("Name") or "").strip(), r.get("Type"), norm(r.get("Artist") or "")
            if not name:
                continue
            if kind == "Artist":
                self.names.add(norm(name))
            elif kind == "Album":
                self.albums.add((norm(clean_title(name)), by))
            elif kind == "Song":
                self.songs.add((norm(name), by))
            elif kind == "Genre":
                self.genres.add(name.strip().casefold())

    @staticmethod
    def _match(pairs, title, artist):
        t, a = norm(title), norm(artist)
        return (t, "") in pairs or (t, a) in pairs

    def name_blocked(self, name: str) -> bool:
        return norm(name) in self.names

    def album_blocked(self, title: str, artist: str = "") -> bool:
        return self._match(self.albums, clean_title(title), artist)

    def song_blocked(self, title: str, artist: str = "") -> bool:
        return bool(self.songs) and self._match(self.songs, title, artist)

    def genre_blocked(self, genres) -> str:
        """The artist's main (first) genre, when it is blocked."""
        return genres[0] if genres and genres[0].casefold() in self.genres else ""

    def tag_blocked(self, tags) -> str:
        """The first of the three strongest tags (weight 50+) that matches a blocked pattern (else '')."""
        strong = [n for n, w in sorted(tags or [], key=lambda t: -t[1])[:3] if w >= 50]
        return next((n for n in strong if any(p.search(n) for p in self.patterns)), "")
