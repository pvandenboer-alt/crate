"""Deezer's public catalogue API (no key or login): artist photos and album covers when Spotify
can't be used, for example while it rate-limits us."""
import re
import time

import requests

from .text import clean_title, compact, loose, norm

API = "https://api.deezer.com"


def song_key(title) -> str:
    """A song title without (feat. ...), (Remastered ...) or ' - Radio Edit' style additions."""
    t = re.sub(r"\s*[\(\[][^)\]]*[\)\]]", "", str(title or ""))
    return norm(re.split(r"\s+-\s+", t)[0])


class Deezer:
    def __init__(self, session=None, min_interval: float = 0.15):
        self.s = session or requests.Session()
        self.min_interval, self._last = min_interval, 0.0

    def get(self, path: str, **params):
        for attempt in range(4):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                d = self.s.get(API + path, params=params, timeout=20).json()
            except (requests.RequestException, ValueError):
                time.sleep(2 ** attempt)
                continue
            if isinstance(d, dict) and d.get("error"):
                if (d["error"] or {}).get("code") == 4:   # quota: 50 requests per 5 seconds
                    time.sleep(5)
                    continue
                return {}
            return d
        return {}

    @staticmethod
    def _real(url):
        # Deezer's "no image" placeholder has an empty hash: .../images/artist//500x500-...
        return url if url and "//500x500" not in url and "/artist//" not in url and "/cover//" not in url else ""

    def artist_image(self, name: str, aliases=()):
        wanted = {loose(name), *(loose(a) for a in aliases)}
        for a in self.get("/search/artist", q=name, limit=10).get("data", []):
            if loose(a.get("name")) in wanted:
                return self._real(a.get("picture_xl") or a.get("picture_big") or "")
        return ""

    def find_artist(self, name: str, aliases=(), known_albums=(), known_songs=()):
        """Deezer's ID for this artist, or None. Names alone often match another act (a namesake, or a
        punctuation variant), so a candidate only counts when its catalogue includes an album you have or its
        top tracks include a song you've played."""
        wanted = {loose(name), *(loose(a) for a in aliases)}
        hits = [a for a in self.get("/search/artist", q=name, limit=10).get("data", [])
                if loose(a.get("name")) in wanted or compact(a.get("name")) in {compact(w) for w in wanted}]
        albums = {norm(clean_title(t)) for t in known_albums} - {""}
        songs = {song_key(t) for t in known_songs} - {""}
        for a in sorted(hits, key=lambda a: (loose(a.get("name")) not in wanted, -(a.get("nb_fan") or 0))):
            if albums and albums & {norm(clean_title(x.get("title"))) for x in self.albums(a["id"])}:
                return a["id"]
            if songs and songs & {song_key(t.get("title")) for t in self.get(f"/artist/{a['id']}/top", limit=50).get("data", [])}:
                return a["id"]
        return None

    def album(self, album_id):
        """One release in full: label, nb_tracks, duration (seconds), tracks, contributors, release_date."""
        return self.get(f"/album/{album_id}") or {}

    def albums(self, artist_id, limit: int = 100):
        """The artist's releases (albums, EPs, singles), each with title, record_type, release_date, cover_xl."""
        return self.get(f"/artist/{artist_id}/albums", limit=limit).get("data", [])

    def album_cover(self, artist: str, title: str):
        want = norm(clean_title(title))
        for a in self.get("/search/album", q=f'artist:"{artist}" album:"{title}"', limit=10).get("data", []):
            if norm(clean_title(a.get("title"))) == want and norm((a.get("artist") or {}).get("name")) == norm(artist):
                return self._real(a.get("cover_xl") or a.get("cover_big") or "")
        return ""
