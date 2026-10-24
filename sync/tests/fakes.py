"""In-memory stand-ins for Notion, Last.fm and Spotify, plus a synthetic Spotify export."""
import json
import random
import uuid
from datetime import datetime, timedelta, timezone

from musiclib import notion as N
from musiclib.spotify import Spotify
from musiclib.text import norm

DS = {"artists": "ds-artists", "albums": "ds-albums", "new_releases": "ds-releases", "blocklist": "ds-blocklist"}
KEY_OF = {v: k for k, v in DS.items()}


class FakeNotion:
    def __init__(self):
        self.pages = {k: {} for k in DS}
        self.creates = self.updates = 0

    def validate(self, ds_ids):
        return {k: {n: {"type": t} for n, t in props.items()} for k, props in N.SCHEMA.items()}

    def _typed(self, props):
        return {name: {"type": next(iter(v)), **v} for name, v in props.items()}

    def query_all(self, ds_id, filter=None):
        return [{"object": "page", "id": pid, "properties": dict(p)} for pid, p in self.pages[KEY_OF[ds_id]].items()]

    def create(self, ds_id, properties):
        pid = str(uuid.uuid4())
        self.pages[KEY_OF[ds_id]][pid] = self._typed(properties)
        self.creates += 1
        return {"id": pid}

    def update(self, page_id, properties):
        for db in self.pages.values():
            for pid, p in db.items():
                if pid.replace("-", "") == page_id.replace("-", ""):
                    p.update(self._typed(properties))
                    self.updates += 1
                    return {"id": pid}
        raise KeyError(page_id)

    def request(self, method, path, body=None):
        if method == "PATCH" and path.startswith("/pages/") and (body or {}).get("in_trash"):
            pid = path.split("/")[2].replace("-", "")
            for db in self.pages.values():
                for k in [k for k in db if k.replace("-", "") == pid]:
                    del db[k]
            return {}
        raise NotImplementedError(method + " " + path)

    def block(self, name, kind, artist=""):
        props = {"Name": N.title(name), "Type": N.select(kind)}
        if artist:
            props["Artist"] = N.text(artist)
        return self.create(DS["blocklist"], props)["id"]

    def seed_artist(self, **props):
        return self.create(DS["artists"], props)["id"]

    def rows(self, key):
        return [N.row({"id": pid, "properties": p}) for pid, p in self.pages[key].items()]


class FakeLastFm:
    def __init__(self, scrobbles=(), tags=None, similar=None, latest=None):
        self._scrobbles = list(scrobbles)
        self._tags = tags or {}
        self._similar = similar or {}
        self._latest = latest

    def latest_scrobble(self):
        if self._latest is not None:
            return self._latest
        return max((datetime.fromtimestamp(s["uts"], timezone.utc) for s in self._scrobbles), default=None)

    def scrobbles(self, since_uts, until_uts=None):
        return sorted((s for s in self._scrobbles if s["uts"] > since_uts), key=lambda s: s["uts"])

    def top_tags(self, artist):
        return self._tags.get(artist, [("rock", 100)])

    def similar(self, artist, limit=100):
        return self._similar.get(artist, [])

    def artist_info(self, artist):
        return (f"{artist} is a band. " * 5).strip(), f"mbid-{norm(artist)}"

    def album_cover(self, artist, album):
        return f"https://lastfm.freetls.fastly.net/i/u/600x600/{abs(hash((artist, album))) % 10**8}.jpg"


class FakeSpotify:
    image = staticmethod(Spotify.image)

    def __init__(self, releases=None):
        self.releases = releases or {}

    def _artist(self, name):
        return {"id": "sp" + norm(name).replace(" ", ""), "name": name,
                "images": [{"url": f"https://i.scdn.co/image/ab6761610000e5eb{norm(name).replace(' ', '')}", "width": 640}]}

    def find_artist(self, name, aliases=()):
        return self._artist(name)

    def artist(self, artist_id):
        return {"id": artist_id, "name": artist_id, "images": []}

    def find_album(self, artist, title):
        return {"id": "al" + norm(title).replace(" ", ""), "name": title, "album_type": "album", "total_tracks": 10,
                "release_date": "2004-05-01", "release_date_precision": "day", "images": []}

    blocked = ""

    def tracks(self, ids):
        return {}

    def artists(self, ids):
        return {}

    def artist_releases(self, artist_id, since):
        return [a for a in self.releases.get(artist_id, []) if a["release_date"] >= since]


# ---------- synthetic export ----------
def _row(ts, artist, album, track, ms=200_000, **extra):
    r = {"ts": ts.strftime("%Y-%m-%dT%H:%M:%SZ"), "ms_played": ms, "master_metadata_track_name": track,
         "master_metadata_album_artist_name": artist, "master_metadata_album_album_name": album,
         "spotify_track_uri": f"spotify:track:{abs(hash((artist, track))) % 10**12}", "episode_name": None,
         "audiobook_title": None}
    r.update(extra)
    return r


def synthetic_export(path, seed=7):
    """Writes two Streaming_History_Audio files and returns their paths."""
    rnd = random.Random(seed)
    start = datetime(2015, 1, 1, tzinfo=timezone.utc)
    plan = [
        # (credit, album, tracks, plays)
        ("Postman", "Documenten", 8, 60),
        ("Postmen", "Revival", 6, 40),
        ("Sticks", "Vinger Aan De Pols", 7, 50),
        ("Sticks & ART", "Een Stap Vooruit", 5, 25),
        ("Kubus & Sticks", "Dingen Die Je Niet Ziet", 4, 15),
        ("Opgezwolle", "Eigen Wereld (Remastered)", 6, 45),
        ("Opgezwolle", "Eigen Wereld - Remastered 2003", 2, 10),
        ("Fakkelbrigade", "Brandstichters", 5, 30),
        ("De Jeugd van Tegenwoordig", "Parels Voor De Zwijnen (Deluxe Edition)", 6, 40),
        ("De Jeugd van Tegenwoordig & Diggy Dex", "Collab", 1, 6),
        ("Simon & Garfunkel", "Bridge Over Troubled Water", 6, 30),
        ("Radiohead", "OK Computer", 9, 120),
        ("Radiohead", "In Rainbows (Live from the Basement)", 4, 12),
        ("Encanto Cast", "Encanto (Original Motion Picture Soundtrack)", 5, 40),
        ("One Hit", "Single", 1, 19),
        ("Few Tracks", "Sampler", 5, 6),
    ]
    rows = []
    for credit, album, n_tracks, n_plays in plan:
        for i in range(n_plays):
            ts = start + timedelta(days=rnd.randint(0, 3500), seconds=rnd.randint(0, 86000))
            rows.append(_row(ts, credit, album, f"{album} track {i % n_tracks + 1}"))
    # Things that must not count.
    rows.append(_row(start, "Radiohead", "OK Computer", "Airbag", ms=12_000))
    rows.append(_row(start, None, None, None, spotify_track_uri=None, episode_name="Some podcast"))
    rows.append(_row(start, "Narrator", "Book", "Chapter 1", audiobook_title="A book"))
    rnd.shuffle(rows)
    half = len(rows) // 2
    paths = []
    for i, chunk in enumerate((rows[:half], rows[half:])):
        p = path / f"Streaming_History_Audio_2015-2024_{i}.json"
        p.write_text(json.dumps(chunk), encoding="utf-8")
        paths.append(str(p))
    return paths


class FakeDeezer:
    """Deezer stand-in: every artist is found; releases come from a dict keyed by artist name."""

    def __init__(self, releases=None):
        self.releases = releases or {}

    def find_artist(self, name, aliases=(), known_albums=(), known_songs=()):
        return "dz-" + norm(name).replace(" ", "")

    def albums(self, artist_id):
        return next((v for k, v in self.releases.items() if "dz-" + norm(k).replace(" ", "") == artist_id), [])

    def album(self, album_id):
        for v in self.releases.values():
            for a in v:
                if a.get("id") == album_id:
                    return {**a, "nb_tracks": 2, "duration": 600, "label": "Test Label",
                            "tracks": {"data": [{"title": "One", "duration": 300}, {"title": "Two", "duration": 300}]}}
        return {}

    def artist_image(self, name, aliases=()):
        return ""

    def album_cover(self, artist, title):
        return ""
