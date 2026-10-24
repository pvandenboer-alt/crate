"""Spotify Web API with the client-credentials flow (catalogue data only, no user login).
Development-mode apps get at most 10 search results per type, so every call pages in tens."""
import base64
import re
import time

import requests

from .text import clean_title, loose, norm

API = "https://api.spotify.com/v1"


class SpotifyError(RuntimeError):
    pass


class SpotifyBlocked(SpotifyError):
    """Spotify asked us to stay away for a while: skip it for the rest of this run."""


MAX_WAIT = 60          # seconds; a longer Retry-After means a rate-limit block, not a hiccup
MAX_REQUESTS = 80      # per run: development-mode apps are blocked for a day when they ask too much at once
MIN_INTERVAL = 2.0     # seconds between requests
GRACE = 3 * 3600       # after a block, stay away this much longer than Spotify asked


class Spotify:
    def __init__(self, client_id: str, client_secret: str, market: str = "AU", session=None):
        if not client_id or not client_secret:
            raise SpotifyError("SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET are not set.")
        self.id, self.secret, self.market = client_id, client_secret, market
        self.s = session or requests.Session()
        self.token, self.expires = None, 0.0
        self.min_interval, self._last = MIN_INTERVAL, 0.0   # development-mode apps get a small rolling quota
        self.budget = MAX_REQUESTS                            # requests left this run
        self.blocked = ""                           # why Spotify is off for this run ('' = available)
        self.blocked_until = 0.0                    # epoch seconds, remembered between runs

    def _auth(self):
        if self.token and time.time() < self.expires - 60:
            return
        basic = base64.b64encode(f"{self.id}:{self.secret}".encode()).decode()
        r = self.s.post("https://accounts.spotify.com/api/token", data={"grant_type": "client_credentials"},
                        headers={"Authorization": f"Basic {basic}"}, timeout=30)
        if not r.ok:
            raise SpotifyError(f"Spotify token request failed ({r.status_code}). Check the client ID and secret.")
        d = r.json()
        self.token, self.expires = d["access_token"], time.time() + int(d.get("expires_in", 3600))

    def get(self, path: str, **params):
        if self.blocked:
            raise SpotifyBlocked(self.blocked)
        for attempt in range(6):
            if self.budget <= 0:
                # Not a block: the rest simply waits for the next run (nothing is remembered).
                self.blocked = f"Spotify: this run's {MAX_REQUESTS} requests are used; the rest follows in the next runs"
                raise SpotifyBlocked(self.blocked)
            self.budget -= 1
            self._auth()
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                r = self.s.get(API + path, params=params, headers={"Authorization": f"Bearer {self.token}"}, timeout=30)
            except requests.RequestException:
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 429:
                # Any 429 ends Spotify for this run: retrying is what turns a short limit into a long block.
                retry = float(r.headers.get("Retry-After") or 2)
                if retry > MAX_WAIT:
                    self.blocked_until = time.time() + retry + GRACE
                    self.blocked = f"Spotify rate limit (asked to wait {max(1, round(retry / 60))} min; staying away 3 hours longer)"
                else:
                    self.blocked = "Spotify rate limit (short): the rest follows in the next run"
                raise SpotifyBlocked(self.blocked)
            if r.status_code == 401:
                self.token = None
                continue
            if r.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 404:
                return None
            if not r.ok:
                raise SpotifyError(f"GET {path} -> {r.status_code}: {r.text[:200]}")
            return r.json()
        raise SpotifyError(f"GET {path}: no answer after retries")

    # ---- artists ----
    def find_artist(self, name: str, aliases=()):
        """Search by name and only accept an exact (normalised) name match, most popular first."""
        wanted = {loose(name), *(loose(a) for a in aliases)}
        d = self.get("/search", q=name, type="artist", limit=10, market=self.market) or {}
        for a in d.get("artists", {}).get("items", []):
            if loose(a.get("name")) in wanted:
                return a
        return None

    def artist(self, artist_id: str):
        return self.get(f"/artists/{artist_id}")

    def _batch(self, path, key, ids, size):
        """Several objects per request (50 tracks / 50 artists); one at a time if the batch endpoint isn't allowed."""
        out = {}
        ids = [i for i in dict.fromkeys(ids) if i]
        for n in range(0, len(ids), size):
            chunk = ids[n:n + size]
            try:
                d = self.get(path, ids=",".join(chunk), **({"market": self.market} if key == "tracks" else {})) or {}
                items = d.get(key) or []
            except SpotifyBlocked:
                raise
            except SpotifyError:
                items = [self.get(f"{path}/{i}") for i in chunk]
            for obj in items:
                if obj and obj.get("id"):
                    out[obj["id"]] = obj
                    linked = (obj.get("linked_from") or {}).get("id")   # relinked tracks keep their old ID too
                    if linked:
                        out[linked] = obj
        return out

    def tracks(self, ids):
        return self._batch("/tracks", "tracks", ids, 50)

    def artists(self, ids):
        return self._batch("/artists", "artists", ids, 50)

    @staticmethod
    def image(obj):
        imgs = sorted(obj.get("images") or [], key=lambda i: -(i.get("width") or 0))
        return imgs[0]["url"] if imgs else ""

    # ---- albums ----
    def find_album(self, artist: str, title: str):
        want_title = norm(clean_title(title))
        want_artist = norm(artist)
        for q in (f'album:"{title}" artist:"{artist}"', f"{artist} {title}"):
            d = self.get("/search", q=q, type="album", limit=10, market=self.market) or {}
            for a in d.get("albums", {}).get("items", []):
                if not a:
                    continue
                artists = {norm(x.get("name")) for x in a.get("artists", [])}
                if norm(clean_title(a.get("name"))) == want_title and want_artist in artists:
                    return a
        return None

    def artist_releases(self, artist_id: str, since: str):
        """Albums and singles (not 'appears on') released on or after `since` (YYYY-MM-DD)."""
        out = []
        for group in ("album", "single"):
            offset = 0
            while True:
                d = self.get(f"/artists/{artist_id}/albums", include_groups=group, limit=10, offset=offset,
                             market=self.market) or {}
                items = [a for a in d.get("items", []) if a]
                for a in items:
                    if (a.get("release_date") or "") >= since:
                        out.append(a)
                # Spotify lists newest first within a group; stop once a page is all older.
                if not d.get("next") or not items or all((a.get("release_date") or "") < since for a in items):
                    break
                offset += 10
        return out


def is_live(title: str) -> bool:
    return bool(re.search(r"[\(\[]live\b|\blive (at|in|from|op|aus)\b|^live\b|[-–] live\b", title or "", re.I))


def album_type(sp_album) -> str:
    """Map Spotify's album_type to the Notion Type options (EP = a 'single' with 4 to 6 tracks)."""
    if is_live(sp_album.get("name", "")):
        return "Live"
    t = sp_album.get("album_type")
    if t == "compilation":
        return "Compilation"
    if t == "single":
        return "EP" if 4 <= int(sp_album.get("total_tracks") or 0) <= 6 else "Single"
    return "Album"


def release_date(sp_album) -> str:
    d = sp_album.get("release_date") or ""
    prec = sp_album.get("release_date_precision")
    if prec == "year" or len(d) == 4:
        return f"{d[:4]}-01-01" if d else ""
    if prec == "month" or len(d) == 7:
        return f"{d[:7]}-01"
    return d[:10]
