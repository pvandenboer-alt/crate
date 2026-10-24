import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fakes import DS, FakeDeezer, FakeLastFm, FakeNotion, FakeSpotify, synthetic_export  # noqa: E402
from importer import do_import  # noqa: E402
from musiclib import notion as N  # noqa: E402
from musiclib.library import Blocklist, GenreMap, Resolver, levels  # noqa: E402
from musiclib.pipeline import Run, check_releases, load_yaml  # noqa: E402
from musiclib.plays import Ledger, Play, merge, read_export  # noqa: E402
from musiclib.text import clean_title, credit_parts, norm  # noqa: E402
from weekly import HealthCheckFailed, do_weekly  # noqa: E402

TAGS = {
    "Encanto Cast": [("soundtrack", 100), ("disney", 80)],
    "Postmen": [("reggae", 100), ("nederhop", 70), ("hip-hop", 50), ("dutch", 20)],
    "Sticks": [("nederhop", 100), ("hip-hop", 60)],
    "Opgezwolle": [("nederhop", 100), ("hip-hop", 90), ("dutch", 40)],
    "Radiohead": [("alternative", 100), ("alternative rock", 90), ("rock", 70), ("electronic", 50), ("indie", 40)],
}
SIMILAR = {"Sticks": [("Opgezwolle", 1.0), ("Fakkelbrigade", 0.8), ("Unknown", 0.7)],
           "Opgezwolle": [("Sticks", 1.0)]}


GENRES_FILE = Path(__import__("tempfile").mkdtemp()) / "genres.json"


def make_run(notion, dry, lastfm=None, spotify=None, deezer=None):
    cfg = load_yaml("config.yml")
    cfg["notion"] = DS
    # Fixed thresholds, so tuning config.yml doesn't change what the tests expect.
    cfg["thresholds"].update(artist_plays=20, artist_tracks=5, album_tracks=3, album_plays=8,
                             weekly_artist_plays=10, weekly_album_tracks=3)
    cfg.update(lastfm_user="tester", timezone="Australia/Sydney", scrobbles_from="2026-10-19T00:00:00+11:00")
    run = Run(cfg, notion, lastfm or FakeLastFm(tags=TAGS, similar=SIMILAR), spotify or FakeSpotify(), dry,
              GenreMap(load_yaml("genre_map.yml")), Blocklist(load_yaml("blocklist.yml")))
    run.genres_path = GENRES_FILE   # never the repo's own state files
    run.spotify_path = GENRES_FILE.with_name("spotify.json")
    run.deezer, run.deezer_path = deezer or FakeDeezer(), GENRES_FILE.with_name("deezer.json")
    return run


def seeded():
    n = FakeNotion()
    n.seed_artist(Name=N.title("Sticks"), Aliases=N.text("Sticks & ART; Sticks & Delic; Kubus & Sticks"),
                  Status=N.select("Keep"), **{"Watch releases": N.checkbox(True), "Source": N.multi(["Manual"])})
    n.seed_artist(Name=N.title("Postmen"), Aliases=N.text("Postman"), Status=N.select("Keep"),
                  **{"Watch releases": N.checkbox(True), "Source": N.multi(["Manual"])})
    return n


# ---------- unit ----------
def test_norm_and_titles():
    assert norm("Sticks & ART") == norm("sticks and art")
    assert norm("Beyoncé") == "beyonce"
    assert clean_title("Eigen Wereld (Remastered)") == "Eigen Wereld"
    assert clean_title("Eigen Wereld - Remastered 2003") == "Eigen Wereld"
    assert clean_title("Parels Voor De Zwijnen (Deluxe Edition)") == "Parels Voor De Zwijnen"
    assert clean_title("Abbey Road (Remastered) [Deluxe]") == "Abbey Road"
    assert clean_title("In Rainbows (Live from the Basement)") == "In Rainbows (Live from the Basement)"
    assert clean_title("Bad (Standard version)") == "Bad"
    assert clean_title("Leren Leven EP") == clean_title("Leren Leven - EP") == "Leren Leven"
    assert credit_parts("A feat. B & C") == ["A"]
    assert credit_parts("A & B") == ["A", "B"]
    from musiclib.text import loose
    assert loose("DJ Paul Elstak") == loose("Paul Elstak") and loose("The National") == "national"
    assert loose("Djo") == "djo" and loose("The The") == "the"


def test_export_filters(tmp_path):
    plays, stats = read_export(synthetic_export(tmp_path))
    assert stats["short"] == 1 and stats["podcast"] == 2
    assert all(p.source == "export" for p in plays)


def test_merge_dedupes_by_start_or_end_within_60s():
    t = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)
    export = [Play(t, t + timedelta(seconds=200), "A", "X", "Song", "export")]
    scrob = [Play(t + timedelta(seconds=30), None, "A", "X", "Song", "lastfm"),          # same play
             Play(t + timedelta(seconds=230), None, "A", "X", "song", "lastfm"),         # near the end time
             Play(t + timedelta(minutes=10), None, "A", "X", "Song", "lastfm")]          # a real replay
    merged, dupes = merge(export, scrob)
    assert dupes == 2 and len(merged) == 2


def test_levels_split():
    lv = levels([(i, 100 - i) for i in range(20)], 0.10, 0.30)
    assert [lv[i] for i in (0, 1, 2, 7, 8)] == ["A whole lot", "A whole lot", "Quite a bit", "Quite a bit", "A bit"]


def test_resolver_rules():
    rows = [{"id": "s", "Name": "Sticks", "Aliases": "Kubus & Sticks"}]
    plays = {"de jeugd van tegenwoordig": 40, "de jeugd van tegenwoordig and diggy dex": 6, "boef": 2,
             "boef en de gelogeerde aap": 900, "simon": 1}
    r = Resolver(rows, plays)
    assert r.resolve("Kubus & Sticks").rule == "alias"
    assert r.resolve("Sticks feat. Typhoon").key == "page:s"
    assert r.resolve("De Jeugd van Tegenwoordig & Diggy Dex").key == "new:dejeugdvantegenwoordig"
    assert r.resolve("Simon & Garfunkel").key == "new:simonandgarfunkel"
    assert r.resolve("Boef en de Gelogeerde Aap").key == "new:boefendegelogeerdeaap"
    assert r.resolve("Kill Them With Colour").key == "new:killthemwithcolour"
    assert r.resolve("10 Ft. Ganja Plant").key == "new:10ftganjaplant"
    assert r.resolve("Gold Band").key == r.resolve("Goldband").key


# ---------- end to end ----------
def test_import_dry_run_writes_nothing(tmp_path):
    n = seeded()
    files = synthetic_export(tmp_path)
    before = n.creates
    report = "\n".join(do_import(make_run(n, dry=True), files, ledger_path=tmp_path / "l.gz"))
    assert n.creates == before and n.updates == 0
    assert "Postman" in report and "alias" in report
    assert "Encanto Cast" in report and "top tag 'soundtrack'" in report
    assert not (tmp_path / "l.gz").exists()


def test_import_real_run_is_idempotent(tmp_path):
    n = seeded()
    files = synthetic_export(tmp_path)
    ledger = tmp_path / "ledger.json"
    do_import(make_run(n, dry=False), files, ledger_path=ledger)
    artists = {r["Name"]: r for r in n.rows("artists")}
    albums = n.rows("albums")

    # Known variants roll up; separate artists stay separate; duo stays one; blocked/below threshold skipped.
    assert "Postman" not in artists and "Sticks & ART" not in artists and "Kubus & Sticks" not in artists
    assert {"Postmen", "Sticks", "Opgezwolle", "Fakkelbrigade", "Radiohead", "Simon & Garfunkel",
            "De Jeugd van Tegenwoordig", "Few Tracks"} <= set(artists)
    assert "Encanto Cast" not in artists and "One Hit" not in artists
    assert artists["Postmen"]["Plays"] == 100 and artists["Sticks"]["Plays"] == 90
    assert artists["Postmen"]["Status"] == "Keep"  # seeded row keeps its status
    assert "Check: multi-artist" in (artists["De Jeugd van Tegenwoordig"]["Notes"] or "")
    assert artists["Radiohead"]["Genres"] == ["Alternative rock", "Rock", "Electronic"]
    assert artists["Sticks"]["Genres"] == ["Nederhop", "Hip-hop"]
    assert artists["Radiohead"]["Image"].startswith("https://i.scdn.co/")
    sim = {r["id"].replace("-", "") for r in n.rows("artists") if r["Name"] in ("Opgezwolle", "Fakkelbrigade")}
    assert {i.replace("-", "") for i in artists["Sticks"]["Similar artists"]} == sim
    assert all(r["Level"] in ("A whole lot", "Quite a bit", "A bit") for r in artists.values())
    top = __import__("json").loads(artists["Radiohead"]["Top songs"])
    assert top[0][0].startswith("OK Computer track") and len(top) == 10
    assert all(r["Full listens"] is not None for r in n.rows("albums"))
    assert artists["Radiohead"]["Level"] == "A whole lot"
    assert artists["Radiohead"]["Peak year"] and artists["Radiohead"]["First listened"]
    assert artists["Radiohead"]["Bio"].startswith("Radiohead is a band.")
    assert artists["Radiohead"]["MusicBrainz ID"] == "mbid-radiohead"
    months = artists["Postmen"]["Plays by month"]
    assert sum(int(n) for part in months.split("; ") for n in part.split(":")[1].split(".")) == 100
    years = dict(x.split(":") for x in artists["Postmen"]["Plays by year"].split("; "))
    assert sum(map(int, years.values())) == 100

    titles = sorted(r["Name"] for r in albums)
    assert titles.count("Eigen Wereld") == 1          # remaster variants merged
    assert "In Rainbows (Live from the Basement)" in titles
    ew = next(r for r in albums if r["Name"] == "Eigen Wereld")
    assert ew["Plays"] == 55 and "Eigen Wereld - Remastered 2003" in ew["Title variants"]
    art = next(r for r in albums if r["Name"] == "Een Stap Vooruit")
    assert art["Credited as"] == "Sticks & ART"
    assert [i.replace("-", "") for i in art["Artist"]] == [artists["Sticks"]["id"].replace("-", "")]
    live = next(r for r in albums if r["Name"].startswith("In Rainbows"))
    assert live["Type"] == "Live"
    assert all(r["Cover"] for r in albums)

    # Second run: nothing new, nothing changes.
    creates, updates = n.creates, n.updates
    run2 = make_run(n, dry=False)
    do_import(run2, files, ledger_path=ledger)
    assert n.creates == creates
    assert n.updates == updates, run2.stats


def test_weekly_sync(tmp_path):
    n = seeded()
    files = synthetic_export(tmp_path)
    ledger = tmp_path / "ledger.json"
    do_import(make_run(n, dry=False), files, ledger_path=ledger)

    now = datetime(2026, 11, 2, 19, 0, tzinfo=timezone.utc)
    scrob = []
    for i in range(12):   # a new artist, 12 plays this month across 4 tracks of one album
        scrob.append({"uts": int((now - timedelta(days=3, hours=i)).timestamp()), "artist": "Frenna",
                      "album": "Francis", "track": f"Song {i % 4}"})
    scrob.append({"uts": int((now - timedelta(days=1)).timestamp()), "artist": "Postman", "album": "Documenten",
                  "track": "Documenten track 1"})
    deezer = FakeDeezer({"Sticks": [
        {"id": 1, "title": "Losse Single", "record_type": "single", "release_date": "2026-10-29"},
        {"id": 2, "title": "Nieuwe EP", "record_type": "ep", "release_date": "2026-10-30", "cover_xl": "https://cdn-images.dzcdn.net/images/cover/x/1000x1000-0.jpg"},
        {"id": 3, "title": "Oud", "record_type": "album", "release_date": "2010-01-01"},
    ]})
    lf = FakeLastFm(scrob, tags=TAGS, similar=SIMILAR)
    added = do_weekly(make_run(n, dry=False, lastfm=lf, deezer=deezer), now, ledger_path=ledger)
    artists = {r["Name"]: r for r in n.rows("artists")}
    assert artists["Frenna"]["Status"] == "Review" and artists["Frenna"]["Plays"] == 12
    assert artists["Postmen"]["Plays"] == 101 and artists["Postmen"]["Last listened"] == "2026-11-02"
    assert any(r["Name"] == "Francis" and r["Status"] == "Review" for r in n.rows("albums"))
    rel = n.rows("new_releases")
    assert [r["Name"] for r in rel] == ["Nieuwe EP"] and rel[0]["Status"] == "New" and rel[0]["Type"] == "EP"
    assert rel[0]["Cover"].startswith("https://cdn-images.dzcdn.net/") and rel[0]["Spotify ID"] == "alnieuweep"   # link attached
    assert artists["Sticks"]["Last release check"] == "2026-11-03"
    assert added
    archive = tmp_path / "scrobbles.jsonl"   # every scrobble with its time, next to the ledger
    assert len(archive.read_text().splitlines()) == 13

    # Run again a day later: no double counting, no duplicate releases.
    do_weekly(make_run(n, dry=False, lastfm=lf, deezer=deezer), now + timedelta(days=1), ledger_path=ledger)
    artists = {r["Name"]: r for r in n.rows("artists")}
    assert artists["Postmen"]["Plays"] == 101 and artists["Frenna"]["Plays"] == 12
    assert len(n.rows("new_releases")) == 1
    assert len(archive.read_text().splitlines()) == 13


def test_health_check_fails_when_scrobbles_stop(tmp_path):
    now = datetime(2026, 11, 2, tzinfo=timezone.utc)
    lf = FakeLastFm(latest=now - timedelta(days=9))
    with pytest.raises(HealthCheckFailed):
        do_weekly(make_run(seeded(), dry=True, lastfm=lf), now, ledger_path=tmp_path / "x.gz")


def test_notion_blocklist(tmp_path):
    n = seeded()
    n.block("Fakkelbrigade", "Artist")
    n.block("Bridge Over Troubled Water", "Album", "Simon & Garfunkel")
    n.block("Documenten track 1", "Song", "Postman")
    n.block("Alternative rock", "Genre")
    files = synthetic_export(tmp_path)
    report = "\n".join(do_import(make_run(n, dry=False), files, ledger_path=tmp_path / "l.gz"))
    artists = {r["Name"]: r for r in n.rows("artists")}
    albums = {r["Name"] for r in n.rows("albums")}
    assert "Fakkelbrigade" not in artists                      # artist on the Notion blocklist
    assert "Radiohead" not in artists                          # main genre Alternative rock is blocked
    assert "Simon & Garfunkel" in artists and "Bridge Over Troubled Water" not in albums
    assert artists["Postmen"]["Plays"] < 100                   # plays of the blocked song don't count
    assert "genre Alternative rock is blocked" in report


def test_bio_cleaning():
    from musiclib.lastfm import clean_bio
    raw = ('Postmen is a Dutch band.<br/>Formed in Rotterdam in 1995. <a href="https://www.last.fm/music/Postmen">'
           'Read more on Last.fm</a>. User-contributed text is available under the Creative Commons By-SA License.')
    assert clean_bio(raw) == "Postmen is a Dutch band.\nFormed in Rotterdam in 1995."
    assert clean_bio("There are multiple artists.") == ""
    escaped = "Junte Uiterwijk is a Dutch rapper from Zwolle. &lt;br /&gt;&lt;br /&gt;He was one of the MCs in Opgezwolle."
    assert clean_bio(escaped) == "Junte Uiterwijk is a Dutch rapper from Zwolle. \n\nHe was one of the MCs in Opgezwolle."


def test_ledger_songs_repeat_and_full_listens():
    import json as _json
    from musiclib.library import build
    t0 = datetime(2014, 3, 1, 20, 0, tzinfo=timezone.utc)
    led = Ledger("UTC")
    # An album played start to finish (7 tracks, 4 minutes apart), then once on shuffle.
    for rnd, sh in ((0, False), (1, True)):
        for i in range(7):
            led.add(Play(t0 + timedelta(days=rnd, minutes=4 * i), None, "Kanye West", "808s & Heartbreak", f"Song {i}", "export", sh))
    # A single song on repeat: 20 plays in 20 days, then a few more a year later.
    for d in range(20):
        led.add(Play(t0 + timedelta(days=10 + d, hours=3), None, "Goldband", "Betaalbaar", "Witte Was", "export", False))
    for d in range(3):
        led.add(Play(t0 + timedelta(days=400 + d), None, "Goldband", "Betaalbaar", "Witte Was", "export", False))
    led._close_run()
    _, artists, albums = build(led, [], [])
    album = next(e for e in albums.values() if e.name == "808s & Heartbreak")
    assert album.full == 1                                  # the shuffled pass doesn't count
    songs = _json.loads(next(e for e in artists.values() if e.name == "Goldband").top_songs())
    assert songs[0][:4] == ["Witte Was", "Betaalbaar", 23, 2014] and songs[0][4] == 20 and songs[0][5] == "2014-03"


def test_ledger_round_trip_keeps_songs(tmp_path):
    led = Ledger("UTC")
    t = datetime(2020, 1, 1, tzinfo=timezone.utc)
    for i in range(3):
        led.add(Play(t + timedelta(days=i), None, "A", "B", "C", "export", False))
    led.save(tmp_path / "l.gz")
    again = Ledger.load(tmp_path / "l.gz", "UTC")
    again.add(Play(t + timedelta(days=5), None, "A", "B", "C", "lastfm"))
    s = next(iter(again.credits["a"]["songs"].values()))
    assert s["c"] == 4 and s["r"] == 4


def test_hardcore_is_not_punk():
    gm = GenreMap(load_yaml("genre_map.yml"))
    gabber = [("hardcore", 100), ("gabber", 60), ("dutch", 40), ("rave", 35)]
    elstak = [("happy hardcore", 100), ("hardcore", 80), ("dutch", 30)]
    bad_brains = [("hardcore", 100), ("punk", 80), ("hardcore punk", 60)]
    assert gm.genres_for(gabber) == ["Hardcore"]
    assert gm.genres_for(elstak) == ["Hardcore"]
    assert gm.genres_for(bad_brains) == ["Punk"]
    assert gm.genres_for([("hardcore", 100)]) == []          # no context: no guess


def test_sync_updates_its_own_genres_but_not_manual_ones(tmp_path):
    n = FakeNotion()
    elstak = n.seed_artist(Name=N.title("Paul Elstak"), Status=N.select("Keep"), Genres=N.multi(["Punk"]))
    mine = n.seed_artist(Name=N.title("Neophyte"), Status=N.select("Keep"), Genres=N.multi(["Electronic"]))
    tags = {"Paul Elstak": [("happy hardcore", 100), ("hardcore", 80)], "Neophyte": [("hardcore", 100), ("gabber", 70)]}
    import json as _json
    GENRES_FILE.write_text(_json.dumps({mine.replace("-", ""): ["Punk"]}))   # the sync once set Punk; the user changed it
    run = make_run(n, dry=False, lastfm=FakeLastFm(tags=tags))
    run.load()
    from musiclib.pipeline import enrich
    enrich(run)
    rows = {r["Name"]: r for r in n.rows("artists")}
    assert rows["Paul Elstak"]["Genres"] == ["Hardcore"]        # the sync's own (untracked) genre is corrected
    assert rows["Neophyte"]["Genres"] == ["Electronic"]         # a hand-set genre stays
    assert run.genre_changes == [("Paul Elstak", ["Punk"], ["Hardcore"])]
    assert _json.loads(GENRES_FILE.read_text())[elstak.replace("-", "")] == ["Hardcore"]
    GENRES_FILE.unlink()


def test_spotify_blocks_instead_of_stalling():
    from musiclib.spotify import Spotify, SpotifyBlocked

    class Resp:
        def __init__(self, status, headers=None, data=None):
            self.status_code, self.headers, self._d = status, headers or {}, data or {}
            self.ok = status < 400

        def json(self):
            return self._d

    class Sess:
        calls = 0

        def post(self, *a, **k):
            return Resp(200, data={"access_token": "t", "expires_in": 3600})

        def get(self, *a, **k):
            Sess.calls += 1
            return Resp(429, {"Retry-After": "3600"})

    sp = Spotify("id", "secret", session=Sess())
    sp.min_interval = 0
    with pytest.raises(SpotifyBlocked):
        sp.find_artist("Paul Elstak")
    with pytest.raises(SpotifyBlocked):   # later calls fail fast, without asking Spotify again
        sp.find_artist("Neophyte")
    assert Sess.calls == 1


def test_keyword_genres_and_spotify_names():
    gm = GenreMap(load_yaml("genre_map.yml"))
    assert gm.genres_for([("dutch trap", 100)], 0, curated=True) == ["Nederhop"]
    assert gm.genres_for([("hip hop nl", 100)], 0) == ["Nederhop"]
    assert gm.genres_for([("dutch hip hop", 100), ("pop rap", 100)], 0, curated=True) == ["Nederhop", "Hip-hop"]
    assert gm.genres_for([("levenslied", 60)], 30) == ["Pop"]
    assert gm.genres_for([("dutch", 100)]) == []      # a country isn't a genre


def test_exact_spotify_matching_fixes_name_clashes(tmp_path):
    from musiclib.pipeline import enrich, spotify_exact
    n = FakeNotion()
    dio = n.seed_artist(Name=N.title("Dio"), Status=N.select("Keep"), **{"Spotify ID": N.text("ronnie"),
                        "Image": N.url("https://i.scdn.co/image/ronnie"), "Bio": N.text("Dio was an American heavy metal band.")})
    led = Ledger("UTC")
    t = datetime(2012, 5, 1, tzinfo=timezone.utc)
    for i in range(30):
        led.add(Play(t + timedelta(days=i), None, "Dio", "Rock & Roll", "Tijdmachine", "export", False, "trk1"))

    class ExactSpotify(FakeSpotify):
        blocked = ""

        def tracks(self, ids):
            return {"trk1": {"id": "trk1", "artists": [{"id": "diorno", "name": "Dio"}],
                             "album": {"id": "alb1", "name": "Rock & Roll", "album_type": "album", "total_tracks": 12,
                                       "release_date": "2008-06-01", "release_date_precision": "day",
                                       "images": [{"url": "https://i.scdn.co/image/rockandroll", "width": 640}]}}}

        def artists(self, ids):
            return {"diorno": {"id": "diorno", "genres": ["dutch hip hop"],
                               "images": [{"url": "https://i.scdn.co/image/diorno", "width": 640}]}}

    lf = FakeLastFm(tags={"Dio": [("heavy metal", 100), ("hard rock", 50)]})
    run = make_run(n, dry=False, lastfm=lf, spotify=ExactSpotify())
    run.spotify_path = tmp_path / "spotify.json"
    run.load()
    from musiclib.library import build
    _, artists, albums = build(led, run.artist_rows, run.album_rows)
    for e in albums.values():   # the album row, so its Spotify ID can be filled
        e.row = {"id": n.create(DS["albums"], {"Name": N.title(e.name)})["id"].replace("-", ""), "Name": e.name}
    spotify_exact(run, artists, albums)
    enrich(run)
    row = next(r for r in n.rows("artists") if r["Name"] == "Dio")
    assert row["Spotify ID"] == "diorno" and row["Image"].endswith("/diorno") and row["Bio"] == ""
    assert row["Genres"] == ["Nederhop", "Hip-hop"] or row["Genres"] == ["Nederhop"]
    assert any("Dio" in note for note in run.notes)
    album = n.rows("albums")[0]
    assert album["Spotify ID"] == "alb1" and album["Release date"] == "2008-06-01"


def test_keywords_only_fill_gaps():
    gm = GenreMap(load_yaml("genre_map.yml"))
    netsky = [("drum and bass", 100), ("liquid funk", 60), ("dnb", 40)]
    assert gm.genres_for(netsky) == ["Dance"]                      # no stray Funk
    assert gm.genres_for([("reggaeton", 100), ("hip-hop", 80)]) == ["Hip-hop"]
    assert gm.genres_for([("reggaeton", 100)]) == []              # reggaeton isn't reggae
    assert gm.genres_for([("nl rap", 60)]) == ["Nederhop"]        # unknown tag: keywords fill the gap


def test_block_list_checks_top_three_tags():
    bl = Blocklist(load_yaml("blocklist.yml"))
    assert bl.tag_blocked([("british", 100), ("comedian", 90), ("stand-up", 70)]) == "comedian"
    assert bl.tag_blocked([("calm", 100), ("sleep", 80)]) == "sleep"
    assert bl.tag_blocked([("hip-hop", 100), ("comedy", 20)]) == ""     # a weak tag doesn't block


def test_mixes_and_radio_episodes_are_not_releases():
    from musiclib.pipeline import deezer_type
    assert deezer_type({"record_type": "album", "title": "ASOT 1297 - A State of Trance Episode 1297"}) == ""
    assert deezer_type({"record_type": "album", "title": "Armada Music Year Mix 2025"}) == ""
    assert deezer_type({"record_type": "ep", "title": "Fluxiverse Vol.3"}) == "EP"
    assert deezer_type({"record_type": "album", "title": "Divine Intervention"}) == "Album"
    assert deezer_type({"record_type": "album", "title": "Live at Paradiso"}) == "Live"


def test_release_rules_and_album_info():
    from musiclib.pipeline import skip_reason, release_info
    today, since = date(2026, 10, 22), "2026-09-22"
    a = lambda t, d="2026-10-16", k="album": {"title": t, "record_type": k, "release_date": d}
    assert skip_reason(a("Divine Intervention"), {"nb_tracks": 14}, 9, set(), today, since) == ""
    assert skip_reason(a("Heart on a Wire", "2027-01-22"), {}, 9, set(), today, since) == ""   # coming soon
    assert "placeholder" in skip_reason(a("Old", "4018-05-11"), {}, 9, set(), today, since)
    assert "reissue" in skip_reason(a("Better Living Through Chemistry (30th Anniversary)"), {}, 9,
                                    {"better living through chemistry"}, today, since)
    assert "box set" in skip_reason(a("Atlantic Soul Legends"), {"nb_tracks": 30}, 9, set(), today, since)
    assert "someone else" in skip_reason(a("KPop Demon Hunters"), {"artist": {"id": 5}}, 9, set(), today, since)
    assert "instrumental" in skip_reason(a("Tales From My Hood (Instrumentals)"), {}, 9, set(), today, since)
    info = release_info({"id": 7, "release_date": "2026-10-16", "nb_tracks": 2, "duration": 610, "label": "HEKATE",
                         "tracks": {"data": [{"title": "Hands Up", "duration": 205}]}}, today)
    assert info["Tracks"]["number"] == 2 and info["Length"]["number"] == 10
    assert info["Track list"]["rich_text"][0]["text"]["content"] == '[["Hands Up", 205]]'


def test_namesake_releases_are_taken_back(tmp_path):
    n = seeded()
    sticks = next(r for r in n.rows("artists") if r["Name"] == "Sticks")
    postmen = next(r for r in n.rows("artists") if r["Name"] == "Postmen")
    for who, name in ((sticks, "Nieuwe EP"), (postmen, "Wrong Postmen Album")):
        n.create(DS["new_releases"], {"Name": N.title(name), "Artist": N.relation([who["id"]]), "Status": N.select("New"),
                                      "Type": N.select("Album"), "Release date": N.date("2026-10-16")})

    class Picky(FakeDeezer):   # Deezer only knows the real Sticks; 'Postmen' there is a namesake
        def find_artist(self, name, aliases=(), known_albums=(), known_songs=()):
            return super().find_artist(name) if name == "Sticks" else None
    deezer = Picky({"Sticks": [{"id": 2, "title": "Nieuwe EP", "record_type": "ep", "release_date": "2026-10-16"}]})
    run = make_run(n, dry=False, deezer=deezer)
    run.deezer_path = tmp_path / "deezer.json"
    run.load()
    check_releases(run, date(2026, 10, 22))
    rel = {r["Name"]: r for r in n.rows("new_releases")}
    assert "Wrong Postmen Album" not in rel and rel["Nieuwe EP"]["Label"] == "Test Label" and rel["Nieuwe EP"]["Tracks"] == 2
    assert json.loads((tmp_path / "deezer.json").read_text())["_v"] == 3


def test_a_merged_deezer_profile_doesnt_list_the_other_acts_albums():
    from musiclib.pipeline import skip_reason
    today, since = date(2026, 10, 22), "2026-09-22"
    turkish = {"title": "BANA BAK", "record_type": "album", "release_date": "2026-10-19"}
    full = {"tracks": {"data": [{"title": "Bana Bak"}, {"title": "Aşkın Dili"}, {"title": "Gözyaşı"}]}}
    known = "U-niq Welkom Eeuwige Jeugd Tunnelvisie Horen, Zien & Schrijven"
    assert "another act" in skip_reason(turkish, full, 9, set(), today, since, known)
    assert skip_reason(turkish, full, 9, set(), today, since, known + " Gözyaşı") == ""   # you do play Turkish U-NIQ
    dutch = {"title": "Rotterdam", "record_type": "album", "release_date": "2026-10-19"}
    assert skip_reason(dutch, {"tracks": {"data": [{"title": "Één Keer"}]}}, 9, set(), today, since, known) == ""


class _Resp:
    def __init__(self, status=200, data=None, headers=None):
        self.status_code, self._data, self.headers = status, data or {}, headers or {}
        self.ok, self.text = status < 400, ""

    def json(self):
        return self._data


class _Session:
    """Spotify stand-in: answers GETs from a list of responses and counts them."""
    def __init__(self, answers):
        self.answers, self.gets = list(answers), 0

    def post(self, *a, **k):
        return _Resp(data={"access_token": "t", "expires_in": 3600})

    def get(self, *a, **k):
        self.gets += 1
        return self.answers.pop(0) if self.answers else _Resp(data={"ok": True})


def test_spotify_stops_at_its_request_budget():
    import time as _t
    from musiclib import spotify as S
    sp = S.Spotify("id", "secret", session=_Session([]))
    sp.min_interval, sp.budget = 0, 3
    for _ in range(3):
        sp.get("/x")
    with pytest.raises(S.SpotifyBlocked):
        sp.get("/x")
    assert sp.s.gets == 3 and sp.blocked_until == 0   # a budget is not a block
    # A long Retry-After ends Spotify for the run at once (no retries) and adds three hours.
    sp = S.Spotify("id", "secret", session=_Session([_Resp(429, headers={"Retry-After": "86100"})]))
    sp.min_interval = 0
    with pytest.raises(S.SpotifyBlocked):
        sp.get("/x")
    assert sp.s.gets == 1 and sp.blocked_until > _t.time() + 86100 + 3 * 3600 - 5
    # A short one also stops the run, but isn't remembered.
    sp = S.Spotify("id", "secret", session=_Session([_Resp(429, headers={"Retry-After": "5"})]))
    sp.min_interval = 0
    with pytest.raises(S.SpotifyBlocked):
        sp.get("/x")
    assert sp.s.gets == 1 and sp.blocked_until == 0
