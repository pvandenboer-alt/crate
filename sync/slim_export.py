"""Make a copy of your Spotify Extended Streaming History with only what Crate uses.

    python sync/slim_export.py "~/Downloads/Spotify Extended Streaming History" data/

Spotify's files also hold your IP address, device, country, podcasts and audiobooks. This keeps only the
fields the importer reads (time, song, artist, album, track ID, how long you listened, shuffle, how the
play started and ended), drops podcast and audiobook rows, and writes Streaming_History_Audio_*.json
files into the output folder. Your original files are not changed.
"""
import json
import sys
from pathlib import Path

KEEP = ("ts", "ms_played", "master_metadata_track_name", "master_metadata_album_artist_name",
        "master_metadata_album_album_name", "spotify_track_uri", "reason_start", "reason_end", "shuffle", "skipped")
NOT_MUSIC = ("episode_name", "episode_show_name", "spotify_episode_uri", "audiobook_title", "audiobook_uri")


def slim_rows(rows):
    out, dropped = [], 0
    for r in rows:
        if any(r.get(k) for k in NOT_MUSIC) or not r.get("master_metadata_track_name"):
            dropped += 1
            continue
        out.append({k: r.get(k) for k in KEEP})
    return out, dropped


def slim(src: Path, dst: Path) -> dict:
    files = sorted(src.expanduser().glob("Streaming_History_Audio_*.json"))
    if not files:
        raise SystemExit(f"No Streaming_History_Audio_*.json files in {src}. Point this at the unzipped "
                         "'Spotify Extended Streaming History' folder.")
    dst.mkdir(parents=True, exist_ok=True)
    totals = {"files": 0, "plays": 0, "dropped": 0}
    for f in files:
        rows, dropped = slim_rows(json.loads(f.read_text(encoding="utf-8")))
        (dst / f.name).write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        totals["files"] += 1
        totals["plays"] += len(rows)
        totals["dropped"] += dropped
    return totals


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    t = slim(Path(sys.argv[1]), Path(sys.argv[2]))
    print(f"{t['files']} files, {t['plays']} music plays kept, {t['dropped']} podcast, audiobook or empty rows left out.")
