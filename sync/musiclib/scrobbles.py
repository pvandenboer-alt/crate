"""Every Last.fm scrobble with its time, in state/scrobbles.jsonl: one [unix time, artist, album, track]
per line, oldest first. The ledger only keeps counts; the Crate report needs to know when each play
happened, to tell whether a Play in Crate was followed by listening."""
import json
from pathlib import Path

from .text import norm


def load(path) -> list:
    path = Path(path)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, list) and len(r) == 4:
            out.append(r)
    return out


def merged(path, plays):
    """The saved scrobbles plus the plays (from_scrobbles) not saved yet, matched on time and track,
    oldest first. Returns (rows, how many were new)."""
    rows = load(path)
    have = {(r[0], norm(r[3])) for r in rows}
    new = 0
    for p in plays:
        uts = int(p.start.timestamp())
        if (uts, norm(p.track)) in have:
            continue
        have.add((uts, norm(p.track)))
        rows.append([uts, p.artist, p.album, p.track])
        new += 1
    rows.sort(key=lambda r: r[0])
    return rows, new


def save(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
