"""The invented household behind the demo: one Plex server, five accounts, their devices, and a week of
Plex-style log lines. Nothing here comes from a real server; every name, address and token is made up."""
import datetime, random, urllib.parse

PLEX_URL = "http://plex.example:32400"       # what is typed on the setup page; run.py sends it to the fake server
MACHINE = "d3m0" * 10
VERSION = "1.42.2.10156-f737b826c"
WARMER_IP = "192.0.2.5"                      # the address the fake server "sees" Hub Warmer's requests from

LIBRARIES = {"1": ("Movies", "movie"), "2": ("TV Shows", "show"), "3": ("Music", "artist"), "4": ("Kids", "movie")}
COLLECTIONS = {
    "1": {"5101": "Staff Picks", "5102": "Award Winners", "5103": "Trending This Week", "5104": "90s Action",
          "5105": "Space and Sci-Fi", "5106": "Holiday Favourites", "5107": "Documentary Shelf"},
    "2": {"5201": "Cosy Mysteries", "5202": "Binge-Worthy", "5203": "Weekend Comedies"},
    "4": {"5401": "Animated Classics", "5402": "Family Movie Night", "5403": "Bedtime Stories"},
}
# hub identifier -> typical time (ms) the fake server takes to build that row when it is not cached
ROW_MS = {
    "movie.recentlyadded.1": 180, "movie.recentlyreleased.1": 240, "movie.inprogress.1": 120,
    "movie.topunwatched.1": 420, "movie.genre.1": 310,
    "custom.collection.1.5101": 610, "custom.collection.1.5102": 1240, "custom.collection.1.5103": 980,
    "custom.collection.1.5104": 450, "custom.collection.1.5105": 520, "custom.collection.1.5106": 380,
    "custom.collection.1.5107": 290,
    "tv.recentlyaired.2": 260, "tv.recentlyadded.2": 210, "tv.inprogress.2": 150, "tv.startwatching.2": 480,
    "tv.toprated.2": 350,
    "custom.collection.2.5201": 540, "custom.collection.2.5202": 870, "custom.collection.2.5203": 330,
    "movie.recentlyadded.4": 90, "movie.topunwatched.4": 160,
    "custom.collection.4.5401": 340, "custom.collection.4.5402": 410, "custom.collection.4.5403": 220,
    "music.recentlyadded.3": 45, "music.recentlyplayed.3": 35, "music.mostplayed.3": 60,
    "home.continue": 190, "home.ondeck": 260,
}


def _rows(suffix):
    return [h for h in ROW_MS if h.endswith(suffix) or h.startswith(f"custom.collection{suffix}.")]


_HOME_MOVIES = ["movie.recentlyadded.1", "custom.collection.1.5101", "custom.collection.1.5103", "custom.collection.1.5102"]
_HOME_TV = ["tv.recentlyaired.2", "custom.collection.2.5202", "tv.startwatching.2"]
# screen -> (request, the rows Plex builds for it)
SCREENS = {
    "home-movies": ("/hubs/promoted?contentDirectoryID=1&count=20&excludeContinueWatching=1", _HOME_MOVIES),
    "home-tv": ("/hubs/promoted?contentDirectoryID=2&count=20&excludeContinueWatching=1", _HOME_TV),
    "home": ("/hubs/promoted?count=12&excludeContinueWatching=1&includeMeta=1", _HOME_MOVIES + _HOME_TV),
    "continue": ("/hubs/continueWatching?count=20&includeMeta=1", ["home.continue", "home.ondeck"]),
    "movies": ("/hubs/sections/1?count=12&includeMeta=1", _rows(".1")),
    "tv": ("/hubs/sections/2?count=12&includeMeta=1", _rows(".2")),
    "music": ("/hubs/sections/3?count=12&includeMeta=1", _rows(".3")),
    "kids": ("/hubs/sections/4?count=12&includeMeta=1", _rows(".4")),
}
BY_URL = {url: rows for url, rows in SCREENS.values()}
THUMBS = {hub: [f"/library/metadata/{1000 + 10 * i + k}/thumb/{1760000000 + 10 * i + k}" for k in range(8)]
          for i, hub in enumerate(ROW_MS)}

# app kind -> (X-Plex-Language, X-Plex-Features, how it asks for posters)
APPS = {
    "tv": ("en", "external-media,indirect-media", "width=300&height=450&minSize=1&upscale=1&url={rel}"),
    "mobile": ("en-GB", "external-media,indirect-media,use-etag",
               "width=240&height=360&minSize=1&upscale=1&format=jpeg&url={rel}"),
    "web": ("en", "external-media,indirect-media,hub-style-list", "width=220&height=330&minSize=1&upscale=1&url={abs}"),
}
_TV = ["home-movies", "home-tv", "continue", "movies", "tv"]


def _dev(cid, title, product, platform, ip, app, screens, since=None):
    return {"cid": "demo-" + cid, "title": title, "product": product, "platform": platform, "ip": ip, "app": app,
            "screens": screens, "since": since}      # since: first used this many days ago (None = long ago)


# hours: how likely a visit is in each hour of the day (24 digits); days: only on these days (0 = today)
ACCOUNTS = [
    {"id": "1", "plextv": "10001", "name": "alex", "title": "Alex", "token": "demo-token-alex", "sessions": 13,
     "hours": "000000123332333334564310", "devices": [
         _dev("living-room-tv", "Living Room TV", "Plex for Apple TV", "tvOS", "192.0.2.21", "tv", _TV),
         _dev("alex-iphone", "Alex's iPhone", "Plex for iOS", "iOS", "192.0.2.34", "mobile",
              ["home-movies", "continue", "movies"]),
         _dev("alex-chrome", "Chrome", "Plex Web", "Chrome", "192.0.2.40", "web", ["home", "movies", "tv", "music"])]},
    {"id": "20114", "plextv": "20114", "name": "sam", "title": "Sam", "token": "demo-token-sam", "sessions": 10,
     "hours": "000000011111111224565420", "devices": [
         _dev("bedroom-tv", "Bedroom TV", "Plex for Android (TV)", "Android", "192.0.2.52", "tv", _TV),
         _dev("sam-ipad", "Sam's iPad", "Plex for iOS", "iOS", "192.0.2.57", "mobile",
              ["home-tv", "continue", "kids"], since=3)]},
    {"id": "20387", "plextv": "20387", "name": "jamie", "title": "Jamie", "token": "demo-token-jamie", "sessions": 8,
     "hours": "000001232221122211111000", "devices": [
         _dev("jamie-pixel", "Jamie's Pixel", "Plex for Android (Mobile)", "Android", "203.0.113.18", "mobile",
              ["home-movies", "continue", "tv"]),
         _dev("jamie-firefox", "Firefox", "Plex Web", "Firefox", "203.0.113.44", "web", ["home", "movies", "tv"],
              since=1)]},
    {"id": "21542", "plextv": "21542", "name": "guest-tv", "title": "Guest TV", "token": "demo-token-guest",
     "sessions": 6, "hours": "000000001221111123443100", "days": (-6, -5, -2), "devices": [
         _dev("guest-roku", "Guest Room Roku", "Plex for Roku", "Roku", "192.0.2.63", "tv",
              ["home-movies", "continue", "movies", "kids"])]},
    {"id": "22096", "plextv": "22096", "name": "morgan", "title": "Morgan", "token": "demo-token-morgan",
     "sessions": 6, "hours": "211000000000000001234554", "devices": [
         _dev("morgan-shield", "Shield", "Plex for Android (TV)", "Android", "198.51.100.7", "tv", _TV)]},
]
OWNER = ACCOUNTS[0]
BY_TOKEN = {a["token"]: a for a in ACCOUNTS}
PLAYING = ("20114", "demo-bedroom-tv", "2")       # sam is watching a TV show on the Bedroom TV


def _midnight(now):
    return datetime.datetime.combine(datetime.date.fromtimestamp(now), datetime.time()).timestamp()


def installed_at(now):
    """the demo pretends Hub Warmer was installed four days ago, mid-morning"""
    return _midnight(now) - 4 * 86400 + 9.5 * 3600


def _stamp(t):
    d = datetime.datetime.fromtimestamp(t)
    return d.strftime("%b %d, %Y %H:%M:%S") + f".{d.microsecond // 1000:03d} [14032] DEBUG - "


def history(now, seed=11):
    """A week of 'Plex Media Server.log' up to a few minutes ago: page loads that were slow before Hub Warmer was
    installed and mostly cached after, the cache drops that explain the slow ones, and poster requests."""
    rng = random.Random(seed)
    day0, installed = _midnight(now), installed_at(now)
    blocks, seen, rid = [], set(), [0x4a10]

    def new_rid():
        rid[0] += rng.randint(1, 9)
        return f"{rid[0]:x}"

    def load(t, acct, dev, screen):
        url, rows = SCREENS[screen]
        path, _, query = url.partition("?")
        lang, feat, poster = APPS[dev["app"]]
        key = lambda hub: f"{acct['id']}/{hub}/1{path}/{lang}{feat}/{query}"      # noqa: E731
        warmed, first = t >= installed, (dev["cid"], screen) not in seen
        seen.add((dev["cid"], screen))
        rebuilt = []
        if first:
            rebuilt = rows if dev["since"] is not None else []       # a new client: nothing of it is cached yet
        else:
            p_hit, p_some = (0.90, 0.06) if warmed else (0.38, 0.22)
            x = rng.random()
            if x >= p_hit:
                some = x < p_hit + p_some
                rebuilt = rng.sample(rows, min(len(rows), rng.randint(1, 3))) if some else rows
                cause = "play" if some and rng.random() < 0.7 else \
                    rng.choices(["play", "library", "row", "none"], (40, 25, 10, 25) if warmed else (25, 35, 10, 30))[0]
                if cause != "none":
                    event = {"play": " because of event plex.event.play.session.state.update",
                             "library": " because of event plex.event.library.update", "row": ""}[cause]
                    when = t - rng.uniform(15, 500)
                    blocks.append((when, [f"{_stamp(when)}HubCache: Expiring '{key(rebuilt[0])}'{event}."]))
        cost = {h: max(8, int(ROW_MS[h] * rng.uniform(0.75, 1.35))) for h in rebuilt}
        ms = sum(cost.values()) + rng.randint(35, 160) if cost else int(min(900, 45 + rng.expovariate(1 / 110)))
        if not cost and warmed and rng.random() < 0.012:
            ms = rng.randint(1150, 2600)                              # cached, but the server was busy
        r, peer = new_rid(), f"{dev['ip']}:{rng.randint(40000, 60000)}"
        lines = [f"{_stamp(t)}Request: [{peer} (WAN)] GET {url} (4 live) #{r} GZIP Signed-in Token ({acct['name']})"]
        for i, hub in enumerate(rows):
            at = _stamp(t + 0.01 * (i + 1))
            lines.append(f"{at}[Req#{r}] HubCache: Computed '{key(hub)}' in {cost[hub]} ms." if hub in cost else
                         f"{at}[Req#{r}] HubCache: Retrieving '{key(hub)}' from the cache.")
        lines.append(f"{_stamp(t + ms / 1000)}Completed: [{peer}] 200 GET {url} (4 live) #{r} TLS GZIP {ms}ms 5120 bytes")
        blocks.append((t, lines))
        p_instant = 0.82 if warmed else 0.30                          # posters: already resized or not
        for _ in range(rng.randint(3, 8)):
            pt, x = t + ms / 1000 + rng.uniform(0.05, 1.5), rng.random()
            pms = rng.randint(3, 28) if x < p_instant else rng.randint(31, 78) if x < p_instant + 0.07 else rng.randint(85, 310)
            thumb = rng.choice(THUMBS[rng.choice(rows)])
            q = poster.format(rel=urllib.parse.quote(thumb, safe=""),
                              abs=urllib.parse.quote(f"http://127.0.0.1:32400{thumb}?X-Plex-Token=demo", safe=""))
            r = new_rid()
            blocks.append((pt, [
                f"{_stamp(pt)}Request: [{peer} (WAN)] GET /photo/:/transcode?{q} (4 live) #{r} GZIP Signed-in Token ({acct['name']})",
                f"{_stamp(pt + pms / 1000)}Completed: [{peer}] 200 GET /photo/:/transcode?{q} (4 live) #{r} TLS {pms}ms 20480 bytes"]))

    visits = []
    for acct in ACCOUNTS:
        for d in range(-6, 1):
            if d in acct.get("days", (d,)):
                for _ in range(acct["sessions"] + rng.randint(-1, 2)):
                    hour = rng.choices(range(24), [int(c) for c in acct["hours"]])[0]
                    visits.append((day0 + d * 86400 + hour * 3600 + rng.uniform(0, 3600), acct))
    by_name = {a["name"]: a for a in ACCOUNTS}
    visits += [(now - 7 * 86400 + 900, by_name["jamie"]),             # same hour last week: jamie's "usual hours"
               (now - 1500, by_name["sam"]), (now - 600, by_name["alex"])]
    for t, acct in sorted(visits, key=lambda v: v[0]):
        devs = [d for d in acct["devices"] if d["since"] is None or t >= day0 - d["since"] * 86400]
        dev = rng.choice(devs + devs[:1])
        for screen in dev["screens"][:1] + rng.sample(dev["screens"][1:], rng.randint(1, len(dev["screens"]) - 1)):
            t += rng.uniform(4, 70)
            if t < now - 240:
                load(t, acct, dev, screen)
    blocks.append((now - 230, [f"{_stamp(now - 230)}HubCache: 412 hubs cached, 91.3% hit ratio."]))
    return "\n".join(line for _, lines in sorted(blocks, key=lambda b: b[0]) for line in lines) + "\n"


def seed_stats(now, seed=11):
    """stats.json as it would look after four days of running: the devices seen playing, and the passes made.
    (The load times, cache hits and row costs are not seeded: Hub Warmer reads them from the log itself.)"""
    rng = random.Random(seed + 1)
    installed, runs, t = installed_at(now), [], installed_at(now) + 200
    while t < now - 420:
        hour = datetime.datetime.fromtimestamp(t).hour
        n = rng.randint(31, 35) if 7 <= hour <= 23 else rng.randint(13, 19)       # at night only Home is kept warm
        cold = min(n, int(rng.expovariate(1 / 2.6)))
        cold_s = sum(rng.uniform(0.6, 3.4) for _ in range(cold))
        runs.append({"ts": t, "n": n, "cold": cold, "cold_s": round(cold_s, 2), "errors": 0,
                     "took": round(cold_s + n * rng.uniform(0.03, 0.06), 1), "accounts": 5})
        t += rng.uniform(285, 305)
    posters_daily, day = {}, datetime.date.fromtimestamp(installed)
    while day < datetime.date.fromtimestamp(now):
        built = rng.randint(180, 420)
        posters_daily[day.isoformat()] = {"requests": rng.randint(2100, 2900), "built": built,
                                          "built_seconds": round(built * rng.uniform(0.11, 0.16), 1)}
        day += datetime.timedelta(days=1)
    devices, ipdev = {}, {}
    for a in ACCOUNTS:
        for d in a["devices"]:
            devices.setdefault(a["id"], {})["cid:" + d["cid"]] = {
                "title": d["title"], "product": d["product"], "platform": d["platform"], "model": None,
                "last_seen": now - rng.uniform(3600, 2 * 86400), "ip": d["ip"]}
            ipdev[f"{a['id']}|{d['ip']}"] = "cid:" + d["cid"]
    return {
        "format": 3, "first_run": installed, "runs": runs, "devices": devices, "ipdev": ipdev,
        "totals": {"runs": len(runs), "requests": sum(r["n"] for r in runs), "cold": sum(r["cold"] for r in runs),
                   "cold_seconds": round(sum(r["cold_s"] for r in runs), 1), "errors": 0,
                   "busy_seconds": round(sum(r["took"] for r in runs), 1)},
        "posters_daily": posters_daily,
        "posters": {"requests": sum(p["requests"] for p in posters_daily.values()),
                    "built": sum(p["built"] for p in posters_daily.values()),
                    "built_seconds": round(sum(p["built_seconds"] for p in posters_daily.values()), 1), "errors": 0},
    }
