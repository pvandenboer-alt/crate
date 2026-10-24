"""Last.fm API: scrobbles, tags, similar artists and album covers."""
import html
import re
import time
from datetime import datetime, timezone

import requests

API = "https://ws.audioscrobbler.com/2.0/"
PLACEHOLDER = "2a96cbd8b46e442fc41c2b86b821562f"  # Last.fm's grey star, used when it has no image


class LastFmError(RuntimeError):
    pass


def clean_bio(raw: str, limit: int = 1900) -> str:
    """Plain text from Last.fm's bio HTML, cut at a sentence end before `limit` characters."""
    s = re.sub(r"<a [^>]*>Read more on Last\.fm</a>\.?", "", raw or "", flags=re.I)
    s = re.sub(r"User-contributed text is available under the Creative Commons.*$", "", s, flags=re.I | re.S)
    s = html.unescape(s)   # Last.fm sometimes escapes its HTML (&lt;br /&gt;), so decode before stripping tags
    s = re.sub(r"<br\s*/?>|</p>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n\n", s).strip()
    if len(s) < 40:  # placeholders like "There are multiple artists with this name"
        return ""
    if len(s) > limit:
        cut = s[:limit]
        end = max(cut.rfind(". "), cut.rfind(".\n"))
        s = cut[: end + 1] if end > limit // 2 else cut.rstrip() + "…"
    return s


class LastFm:
    def __init__(self, api_key: str, user: str, min_interval: float = 0.22, session=None):
        if not api_key:
            raise LastFmError("LASTFM_API_KEY is not set.")
        self.key, self.user = api_key, user
        self.s = session or requests.Session()
        self.s.headers["User-Agent"] = "crate-sync/1.0 (github.com/pvandenboer-alt/crate)"
        self.min_interval, self._last = min_interval, 0.0

    def call(self, method: str, **params):
        params = {"method": method, "api_key": self.key, "format": "json", **params}
        for attempt in range(6):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                r = self.s.get(API, params=params, timeout=30)
                data = r.json()
            except (requests.RequestException, ValueError):
                time.sleep(2 ** attempt)
                continue
            err = data.get("error") if isinstance(data, dict) else None
            if err in (8, 11, 16, 29) or r.status_code >= 500:  # temporary errors and rate limits
                time.sleep(2 ** attempt + 1)
                continue
            if err == 6:  # "not found" (unknown artist/album): an empty answer, not a failure
                return {}
            if err:
                raise LastFmError(f"{method}: {data.get('message')} (error {err})")
            return data
        raise LastFmError(f"{method}: no answer after retries")

    # ---- scrobbles ----
    def latest_scrobble(self):
        d = self.call("user.getRecentTracks", user=self.user, limit=1)
        for t in d.get("recenttracks", {}).get("track", []):
            if t.get("date"):
                return datetime.fromtimestamp(int(t["date"]["uts"]), timezone.utc)
        return None

    def scrobbles(self, since_uts: int, until_uts: int | None = None):
        """Every scrobble after since_uts, oldest first, as dicts with uts/artist/album/track."""
        out, page, pages = [], 1, 1
        while page <= pages:
            params = {"user": self.user, "limit": 200, "page": page, "from": since_uts + 1}
            if until_uts:
                params["to"] = until_uts
            d = self.call("user.getRecentTracks", **params).get("recenttracks", {})
            pages = int(d.get("@attr", {}).get("totalPages") or 1)
            tracks = d.get("track", [])
            if isinstance(tracks, dict):
                tracks = [tracks]
            for t in tracks:
                if not t.get("date"):  # "now playing" has no timestamp
                    continue
                out.append({
                    "uts": int(t["date"]["uts"]),
                    "artist": (t.get("artist") or {}).get("#text", ""),
                    "album": (t.get("album") or {}).get("#text", ""),
                    "track": t.get("name", ""),
                })
            page += 1
        out.sort(key=lambda x: x["uts"])
        return out

    # ---- enrichment ----
    def top_tags(self, artist: str):
        d = self.call("artist.getTopTags", artist=artist, autocorrect=1)
        tags = d.get("toptags", {}).get("tag", [])
        if isinstance(tags, dict):
            tags = [tags]
        return [(t["name"].strip().lower(), int(t.get("count") or 0)) for t in tags if t.get("name")]

    def similar(self, artist: str, limit: int = 100):
        d = self.call("artist.getSimilar", artist=artist, limit=limit, autocorrect=1)
        arts = d.get("similarartists", {}).get("artist", [])
        if isinstance(arts, dict):
            arts = [arts]
        return [(a["name"], float(a.get("match") or 0)) for a in arts if a.get("name")]

    def artist_info(self, artist: str):
        """(bio, MusicBrainz ID). The bio is Last.fm's wiki text with links and the licence line removed;
        English first, Dutch when there is no English text."""
        bio, mbid = "", ""
        for lang in ("en", "nl"):
            d = self.call("artist.getInfo", artist=artist, autocorrect=1, lang=lang).get("artist") or {}
            mbid = mbid or d.get("mbid") or ""
            bio = clean_bio((d.get("bio") or {}).get("content") or (d.get("bio") or {}).get("summary") or "")
            if bio:
                break
        return bio, mbid

    def album_cover(self, artist: str, album: str):
        d = self.call("album.getInfo", artist=artist, album=album, autocorrect=1)
        images = d.get("album", {}).get("image", []) if d else []
        best = ""
        for size in ("mega", "extralarge", "large"):
            for im in images:
                u = im.get("#text", "")
                if im.get("size") == size and u and PLACEHOLDER not in u:
                    best = u
                    break
            if best:
                break
        # Last.fm serves any size from the same hash; ask for a 600 px square.
        return best.replace("/i/u/300x300/", "/i/u/600x600/") if best else ""
