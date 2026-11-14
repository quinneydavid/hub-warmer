"""Warmer core: learn each account's hub requests from Plex's HubCache log lines, replay them.

Plex caches hub results per account AND per exact request (path, query, the app's X-Plex-Language and
X-Plex-Features). Library changes and the account's own playback drop those entries, and the next load
rebuilds every row from scratch.
"""
import concurrent.futures, datetime, io, json, os, re, threading, time
import urllib.error, urllib.parse, urllib.request, zipfile
import xml.etree.ElementTree as ET

from . import __version__, config as C
from .events import LibraryEvents
from .settings import Settings
from .store import Store, log

# HubCache key: '<account>/<hub>/<n>/<path...>/<lang+features>/<query>'
HUBKEY = re.compile(r"\[Req#([0-9a-f]+)\] HubCache: (Retrieving|Computed|Adding) '([^']+)'(?: in (\d+) ms)?")
EXPIRE = re.compile(r"HubCache: Expiring '(\d+)/[^']*'(?: because of event ([\w.]+))?")
HITRATIO = re.compile(r"HubCache: (\d+) hubs cached, ([\d.]+)% hit ratio")
REQ = re.compile(r"Request: \[\[?([0-9a-fA-F.:]+?)\]?:\d+[^\]]*\] GET (\S+) .*? #([0-9a-f]+) (?:.*?Token \(([^)]*)\))?")
DONE = re.compile(r"Completed: \[\[?([0-9a-fA-F.:]+?)\]?:\d+\] (\d{3}) GET (\S+) .*? #([0-9a-f]+) (?:.*? )?(\d+)ms ")
TS = re.compile(r"^(\w{3} \d{2}, \d{4} \d{2}:\d{2}:\d{2})")
LANGFEAT = re.compile(r"^([a-z]{2}(?:-[A-Z]{2})?)?(.*)$")
PREFIXES = ("/hubs/promoted", "/hubs/sections/", "/hubs/continueWatching")
POSTER_WORKERS = 3                       # posters requested at the same time


HOME_PREFIXES = ("/hubs/promoted", "/hubs/continueWatching")
COLLECTION_PREFIXES = ("/hubs/promoted", "/hubs/sections/")   # the requests whose rows include collections


def is_home_entry(entry):
    """a learned request (json [url, lang, features]) that belongs to the Home screen, not a library page"""
    return json.loads(entry)[0].split("?", 1)[0].startswith(HOME_PREFIXES)


def has_collection_rows(entry):
    """a learned request whose screen can show collection rows (Home and library pages, not Continue Watching)"""
    return json.loads(entry)[0].split("?", 1)[0].startswith(COLLECTION_PREFIXES)


def entry_sections(entry):
    """Library sections a learned request covers, as a set of ids - or None when it is not limited to
    specific libraries (the combined Home screen, Continue Watching)."""
    url = json.loads(entry)[0]
    path, _, query = url.partition("?")
    m = re.match(r"/hubs/sections/(\d+)", path)
    if m:
        return {m.group(1)}
    if path.startswith("/hubs/promoted"):
        cd = re.search(r"(?:^|&)contentDirectoryID=([^&]+)", query)
        if cd:
            return set(urllib.parse.unquote(cd.group(1)).split(","))
    return None


def photo_profile(path):
    """'/photo/:/transcode?width=..&url=..' as logged by Plex -> (profile json, thumb path) or None.
    The profile is everything that makes up Plex's image cache key except the poster itself: the size
    parameters and whether the app passes the poster as a relative path or as an absolute URL with the
    user's token (in that form the cached image is per user)."""
    q = urllib.parse.parse_qs(path.partition("?")[2])
    url = (q.get("url") or [""])[0]
    m = re.match(r"(https?://[^/]+)?(/library/metadata/\d+/thumb/\d+)(\?.*)?$", url)
    if not m:
        return None
    params = sorted((k, v[0]) for k, v in q.items() if k != "url" and not k.startswith("X-Plex-"))
    return json.dumps({"host": m.group(1) or "", "token": bool(m.group(3) and "X-Plex-Token" in m.group(3)),
                       "params": params}), m.group(2)


def hub_thumbs(body):
    """poster paths of every item in a hub response"""
    try:
        mc = json.loads(body).get("MediaContainer", {})
    except (ValueError, AttributeError):
        return []
    out = []
    for hub in mc.get("Hub", []):
        for item in hub.get("Metadata", []) or []:
            t = item.get("thumb")
            if t and re.match(r"/library/metadata/\d+/thumb/\d+$", t):
                out.append(t)
    return out


def is_row_request(path):
    p = path.split("?", 1)[0]
    return p.startswith(PREFIXES) and "/items" not in p and not p.endswith("/manage")


def get(url, token, timeout=90, lang="", feat="", accept="application/json", client_id="hub-warmer",
        product="Hub Warmer", version=__version__):
    h = {"X-Plex-Token": token, "Accept": accept, "X-Plex-Client-Identifier": client_id,
         "X-Plex-Product": product, "X-Plex-Version": version}
    if lang:
        h["X-Plex-Language"] = lang
    if feat:
        h["X-Plex-Features"] = feat
    t = time.time()
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
        body = r.read()
    return time.time() - t, body


def parse_key(key):
    """HubCache key -> (account, path?query, lang, features) or None"""
    parts = key.split("/")
    if len(parts) < 6 or parts[3] != "hubs":
        return None
    acct, path, langfeat, query = parts[0], "/" + "/".join(parts[3:-2]), parts[-2], parts[-1]
    if not is_row_request(path):
        return None
    m = LANGFEAT.match(langfeat)
    return acct, path + ("?" + query if query else ""), m.group(1) or "", m.group(2) or ""


def describe(url, lang, feat, sections=None):
    """human label for a learned request: ('Movies page', 'web', 'en')"""
    path, _, q = url.partition("?")
    m = re.match(r"/hubs/sections/(\d+)", path)
    if m:
        what = f"{(sections or {}).get(m.group(1), 'Library ' + m.group(1))} page"
    elif path.startswith("/hubs/continueWatching"):
        what = "Continue Watching"
    else:
        cd = re.search(r"contentDirectoryID=([^&]+)", q)
        sec = urllib.parse.unquote(cd.group(1)) if cd else ""
        what = "Home" + (f" · {(sections or {}).get(sec, sec)}" if sec else "")
    app = "web" if "hub-style-list" in feat else ("TV/mobile app" if feat else "app")
    return what, app, lang or "-"


_EPOCH = {}


def _epoch(stamp):
    """'Nov 09, 2026 15:17:49' -> unix time (cached: many log lines share a second)"""
    v = _EPOCH.get(stamp)
    if v is None:
        if len(_EPOCH) > 50000:
            _EPOCH.clear()
        try:
            v = datetime.datetime.strptime(stamp, "%b %d, %Y %H:%M:%S").timestamp()
        except ValueError:
            v = 0.0
        _EPOCH[stamp] = v
    return v


def miss_cause(event, age=0):
    """why Plex had dropped the rows a real user then had to wait for. event: the account's last HubCache
    expiry before the request (None = none in the log), age: seconds between that expiry and the request.
    An expiry older than 15 minutes means a pass could have rebuilt the rows and had not."""
    if event is None:
        return "other"
    if age > 900:
        return "not rewarmed"
    if "play" in event:
        return "own playback"
    if not event:
        return "row changed"
    return "library change"


BUILTIN_ROWS = {"movie.by.actor.or.director": "More by actor / director", "movie.recentlyadded": "Recently Added",
                "movie.recentlyreleased": "Recently Released", "movie.recentlyviewed": "Recently Viewed",
                "movie.inprogress": "Continue Watching", "movie.topunwatched": "Top Unwatched", "movie.genre": "More in genre",
                "movie.curated": "Curated", "tv.recentlyaired": "Recently Aired", "tv.recentlyadded": "Recently Added",
                "tv.inprogress": "Continue Watching", "tv.startwatching": "Start Watching", "tv.toprated": "Top Rated",
                "tv.recentlyviewed": "Recently Viewed", "tv.moreingenre": "More in genre", "tv.ondeck": "On Deck",
                "home.continue": "Continue Watching (Home)", "home.ondeck": "On Deck (Home)",
                "home.playlists": "Playlists (Home)", "continueWatching": "Continue Watching"}


def describe_row(hub, sections=None, collections=None):
    """HubCache hub identifier -> (row name, library name, kind)"""
    m = re.match(r"custom\.collection\.(\d+)\.(\d+)$", hub)
    if m:
        return ((collections or {}).get(m.group(2), f"Collection {m.group(2)}"),
                (sections or {}).get(m.group(1), f"Library {m.group(1)}"), "collection")
    m = re.match(r"(.+?)\.(\d+)$", hub)
    if m:
        return (BUILTIN_ROWS.get(m.group(1), m.group(1)), (sections or {}).get(m.group(2), f"Library {m.group(2)}"),
                "built-in")
    return BUILTIN_ROWS.get(hub, hub), "", "built-in"


def _iso(plex_ts):
    """'Nov 09, 2026 15:17:49' -> '2026-11-09T15:17:49' (Plex server local time)"""
    try:
        return datetime.datetime.strptime(plex_ts, "%b %d, %Y %H:%M:%S").strftime("%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return ""


class Warmer:
    def __init__(self, store: Store):
        self.store = store
        self.S = Settings(store)
        self.wake = threading.Event()
        self.force_next = False
        self.state = "starting"          # starting / waiting-for-setup / idle / warming / error
        self.last_error = ""
        self.tokens, self.tokens_at = {}, 0   # account id -> (name, token), memory only
        self.owner_info = None
        self.machine_id = ""
        self.server_version = ""
        self.sections = {}               # library section id -> title
        self.collections = {}            # collection ratingKey -> title (for the row cost table)
        self.playing = set()             # accounts with a live playback session
        self.after_play = {}             # account -> when its playback ended (warm it right away)
        self.play_ips = {}               # account -> addresses it was last watching from (that device goes first)
        self.play_devs = {}              # account -> device keys ("cid:<client id>") it was last watching on
        self.play_secs = {}              # account -> library sections it is watching right now
        self.photo_queue = []            # [(account, profile json, thumb)] posters still to prepare
        self.photo_done = {}             # image cache identity -> when we last asked for it
        self.bw = {}                     # (account, client id) -> {second: bytes}: Plex's per-device traffic
        self.bw_at = 0
        self.partial = set()             # accounts the current plan warms Home for only
        self.next_check = 0
        self.last_plan = {}
        self.events = LibraryEvents(self)
        self.last_pass_end = 0.0

    # ---------- tokens / identity ----------
    def owner_token(self):
        if C.PLEX_TOKEN:
            return C.PLEX_TOKEN
        tok = self.store.config.get("owner_token")
        if tok:
            return tok
        try:
            with open(C.PLEX_PREFS) as f:
                m = re.search(r'PlexOnlineToken="([^"]+)"', f.read())
            return m.group(1) if m else None
        except OSError:
            return None

    def pms(self, path, otoken, **kw):
        return get(self.S.get("plex_url") + path, otoken, client_id=self.store.client_id, **kw)

    def identity(self, otoken):
        _, body = self.pms("/identity", otoken)
        mc = json.loads(body)["MediaContainer"]
        self.machine_id, self.server_version = mc["machineIdentifier"], mc.get("version", "")
        return self.machine_id

    def plextv(self, url, otoken, **kw):
        """plex.tv call with the owner token. If that token is the SERVER's own token (Preferences.xml, or
        PLEX_TOKEN copied from it), plex.tv records the request's X-Plex-Version as the server's version
        (9 Nov 2026: it showed as '0.2.0' and new Plex apps refused the server as outdated). So unless the token
        came from our own Sign in with Plex, identify as the server itself."""
        if otoken == self.store.config.get("owner_token"):
            return get(url, otoken, client_id=self.store.client_id, **kw)
        if not self.machine_id or not self.server_version:
            self.identity(otoken)
        return get(url, otoken, client_id=self.machine_id, product="Plex Media Server",
                   version=self.server_version, **kw)

    def owner(self, otoken):
        if not self.owner_info:
            _, body = self.plextv("https://plex.tv/api/v2/user", otoken, timeout=30)
            u = json.loads(body)
            self.owner_info = {"id": str(u["id"]), "username": u.get("username") or u.get("title"),
                               "title": u.get("title") or u.get("username"), "thumb": u.get("thumb", "")}
        return self.owner_info

    def refresh_server_info(self, otoken):
        """account names (the server's own list) + library names; cheap, local"""
        try:
            _, body = self.pms("/accounts", otoken)
            names = {str(a["id"]): a.get("name") or str(a["id"])
                     for a in json.loads(body)["MediaContainer"].get("Account", []) if a.get("id")}
            names["1"] = self.owner(otoken)["username"]
            _, body = self.pms("/library/sections", otoken)
            dirs = json.loads(body)["MediaContainer"].get("Directory", [])
            self.sections = {str(d["key"]): d["title"] for d in dirs}
            colls = {}
            for d in dirs:
                if d.get("type") not in ("movie", "show"):
                    continue
                try:
                    _, body = self.pms(f"/library/sections/{d['key']}/collections", otoken, timeout=30)
                    for c in json.loads(body)["MediaContainer"].get("Metadata", []):
                        colls[str(c["ratingKey"])] = c.get("title", "")
                except Exception:
                    pass
            self.collections = colls or self.collections
            ids = dict(self.store.stats.get("plextv_ids", {}))
            ids["1"] = self.owner(otoken)["id"]
            self.store.set_accounts(names, ids)
        except Exception as e:
            log(f"reading server accounts failed: {e!r}")

    def account_tokens(self, otoken, max_age=900):
        """server account id -> (name, token); owner = account 1. Cached 15 min."""
        if self.tokens and time.time() - self.tokens_at < max_age:
            return self.tokens
        machine = self.machine_id or self.identity(otoken)
        names = self.store.stats.get("account_names", {})
        toks = {"1": (self.owner(otoken)["username"], otoken)}
        ids = {"1": self.owner(otoken)["id"]}
        try:
            _, body = self.plextv(f"https://plex.tv/api/servers/{machine}/shared_servers", otoken, timeout=30,
                                  accept="application/xml")
            for s in ET.fromstring(body).iter("SharedServer"):
                uid, tok = s.get("userID"), s.get("accessToken")
                if uid and tok:
                    toks[uid] = (names.get(uid) or s.get("username") or s.get("email") or uid, tok)
                    ids[uid] = uid
        except Exception as e:
            log(f"plex.tv shared_servers failed: {e}")
        self.tokens, self.tokens_at = toks, time.time()
        self.store.set_accounts(dict(names, **{a: n for a, (n, _) in toks.items() if a not in names}), ids)
        return toks

    def shared_user_ids(self):
        return {i for a, i in self.store.stats.get("plextv_ids", {}).items() if a != "1"}

    # ---------- devices (who plays on which device, from which address) ----------
    def update_sessions(self, otoken):
        """Which devices are playing right now (-> number of transcodes). A device counts as playing only
        while its stream is actually running: a paused stream can sit for hours, and pausing is often
        followed by browsing, so a paused device is warmed like any other. When an account's last running
        stream pauses or ends, the account goes to after_play and is warmed straight away."""
        try:
            _, body = self.pms("/status/sessions", otoken, timeout=15)
        except Exception:
            return 0
        players, n_tc, now_playing, cur_ips, cur_devs, cur_secs = [], 0, set(), {}, {}, {}
        for m in json.loads(body)["MediaContainer"].get("Metadata", []):
            p, u = m.get("Player", {}), m.get("User", {})
            if m.get("TranscodeSession"):
                n_tc += 1
            if u.get("id") is None:
                continue
            a = str(u["id"])
            players.append((a, p.get("address", ""),
                            {"title": p.get("title"), "product": p.get("product"), "platform": p.get("platform"),
                             "model": p.get("model"), "cid": p.get("machineIdentifier")}))
            if p.get("state", "playing") == "paused":
                continue
            now_playing.add(a)
            if p.get("address"):
                cur_ips.setdefault(a, set()).add(p["address"])
            if p.get("machineIdentifier"):
                cur_devs.setdefault(a, set()).add("cid:" + p["machineIdentifier"])
            if m.get("librarySectionID") is not None:
                cur_secs.setdefault(a, set()).add(str(m["librarySectionID"]))
        now = time.time()
        for a in now_playing:      # accounts that stopped keep their last devices: those are warmed first
            self.play_ips[a], self.play_devs[a] = cur_ips.get(a, set()), cur_devs.get(a, set())
        if self.S.get("skip_while_playing"):
            for a in self.playing - now_playing:
                self.after_play[a] = now
        self.after_play = {a: t for a, t in self.after_play.items() if now - t < 900 and a not in now_playing}
        self.playing = now_playing
        self.play_secs = cur_secs
        self.store.note_players(players)
        return n_tc

    # ---------- which device made a request ----------
    def poll_bandwidth(self, otoken):
        """Plex keeps per-second traffic per account and device for a few minutes (its dashboard graph).
        The request log only has an address, so this is what tells two devices behind one address apart."""
        if time.time() - self.bw_at < 30:
            return
        self.bw_at = time.time()
        try:
            _, body = self.pms("/statistics/bandwidth?timespan=6", otoken, timeout=20)
        except Exception:
            return
        mc = json.loads(body)["MediaContainer"]
        devices = {d["id"]: d for d in mc.get("Device", []) if d.get("clientIdentifier")}
        catalog = {}
        for r in mc.get("StatisticsBandwidth", []):
            d = devices.get(r.get("deviceID"))
            if not d or d["clientIdentifier"] == self.store.client_id:
                continue
            self.bw.setdefault((str(r["accountID"]), d["clientIdentifier"]), {})[r["at"]] = r["bytes"]
            catalog[d["clientIdentifier"]] = {"name": d.get("name") or "", "platform": d.get("platform") or ""}
        cutoff = time.time() - 1800
        for k in list(self.bw):
            self.bw[k] = {s: b for s, b in self.bw[k].items() if s >= cutoff}
            if not self.bw[k]:
                del self.bw[k]
        known = dict(self.store.stats.get("devcat", {}))
        known.update(catalog)
        self.store.note_device_catalog(known)

    def device_at(self, acct, when):
        """-> client id of the device on this account that was transferring data at that moment, or None.
        A device that is playing always shows traffic, so another device with real traffic wins over it;
        background tools that poll Plex move only a few kB and lose to a page load."""
        cands = []
        for (a, cid), secs in self.bw.items():
            if a == acct:
                b = sum(v for s, v in secs.items() if when - 1 <= s <= when + 3)
                if b:
                    cands.append((b, cid))
        if not cands:
            return None
        playing = {k[4:] for k in self.play_devs.get(acct, ())} if acct in self.playing else set()
        others = sorted((c for c in cands if c[1] not in playing), reverse=True)
        if others and others[0][0] >= 20000 and (len(others) == 1 or others[0][0] >= 3 * others[1][0]):
            return others[0][1]
        if others and others[0][0] >= 20000:
            return None                                   # two busy candidates: don't guess
        mine = sorted((c for c in cands if c[1] in playing), reverse=True)
        return mine[0][1] if mine else None

    # ---------- logs ----------
    def log_sources(self, otoken):
        """-> [callable returning a fresh text stream] for each Plex Media Server log (downloaded once)"""
        logs = self.S.get("plex_logs")
        if logs == "api":
            _, body = self.pms("/diagnostics/logs", otoken, timeout=180, accept="application/zip")
            z = zipfile.ZipFile(io.BytesIO(body))
            return [(lambda i=i: io.TextIOWrapper(z.open(i), errors="replace")) for i in z.infolist()
                    if i.filename.rsplit("/", 1)[-1].startswith("Plex Media Server")
                    and i.filename.endswith(".log")]
        return [(lambda p=os.path.join(logs, n): open(p, errors="replace")) for n in os.listdir(logs)
                if n.startswith("Plex Media Server") and n.endswith(".log")]

    def learn(self, otoken, force=False):
        """Read the Plex logs: learn requests (+ the address they came from), real users' row load times,
        each account's usual hours, Plex's hit ratio."""
        st = self.store
        if self.S.get("plex_logs") == "api" and not force and \
                time.time() - st.learned.get("last_learn", 0) < self.S.get("learn_minutes") * 60:
            return None
        self.refresh_server_info(otoken)
        seen, hub_lines, hit = {}, 0, None
        seen_dev = {}                           # acct -> {entry: client id} where the device could be identified
        photo_seen = {}                         # acct -> {profile json}: how its apps ask for posters
        photo_loads = []                        # real poster requests: (iso ts, ms)
        rows, hits = {}, []                     # hub -> [n, sum_ms, max_ms]; real loads: hit or rebuilt, and why
        known_before = {a: set(e) for a, e in st.learned.get("accounts", {}).items()}
        last_ts = st.stats.get("loads_last_ts", "")
        act_ts = st.stats.get("activity_last_ts", "")
        newest, newest_act = last_ts, act_ts
        loads, events = [], []
        marker = f"hubwarmer_self={st.client_id}"
        sources = self.log_sources(otoken)
        known = st.stats.setdefault("self_ips", {})
        for src in sources:                     # pass 1: our own address(es), from the marked /identity requests
            with src() as fh:
                for line in fh:
                    if marker in line and "Request: [" in line:
                        r = REQ.search(line)
                        if r:
                            known[r.group(1)] = time.time()
        st.stats["self_ips"] = {ip: t for ip, t in known.items() if t > time.time() - 14 * 86400}
        self_ips = self.S.self_ips() | set(st.stats["self_ips"])
        for src in sources:                     # pass 2: learn
            own, req_ip, pending = set(), {}, {}
            real, last_exp = {}, {}             # rid -> this real request's cache result; acct -> (when, event)
            photo_pending = set()               # request ids of real poster requests
            last_play = {}                      # acct -> when its own playback last dropped rows
            with src() as fh:
                for line in fh:
                    if "Request: [" in line:
                        r = REQ.search(line)
                        if not r:
                            continue
                        ip, path, rid, who = r.groups()
                        if ip in self_ips:
                            own.add(rid)
                        elif path.startswith("/photo/:/transcode") and who:
                            photo_pending.add(rid)
                            pp = photo_profile(path)
                            acct = st.account_for_name(who)
                            if pp and acct:
                                c = photo_seen.setdefault(acct, {})
                                c[pp[0]] = c.get(pp[0], 0) + 1
                        elif is_row_request(path):
                            req_ip[rid] = ip
                            acct = st.account_for_name(who) if who else None
                            pending[rid] = acct
                            tm = TS.match(line)
                            ts = _iso(tm.group(1)) if tm else ""
                            real[rid] = {"acct": acct, "t": _epoch(tm.group(1)) if tm else 0, "hit": 0, "miss": 0,
                                         "known": None, "url": path, "rows": []}
                            if acct and ts > act_ts:
                                events.append((acct, ts))
                                newest_act = max(newest_act, ts)
                        continue
                    if "Completed: [" in line:
                        d = DONE.search(line)
                        if d and d.group(4) in photo_pending:
                            photo_pending.discard(d.group(4))
                            tm = TS.match(line)
                            ts = _iso(tm.group(1)) if tm else ""
                            if ts and ts > last_ts:
                                photo_loads.append((ts, int(d.group(5))))
                                newest = max(newest, ts)
                            continue
                        if d and d.group(4) in pending:
                            tm = TS.match(line)
                            ts = _iso(tm.group(1)) if tm else ""
                            acct = pending.pop(d.group(4))
                            r = real.pop(d.group(4), None)
                            if r and ts and ts > last_ts and r["hit"] + r["miss"]:
                                a = r["acct"] or acct
                                if not r["miss"]:
                                    why = "hit"
                                elif r["known"] is False:
                                    why = "new app or screen"
                                else:
                                    le, lp = last_exp.get(a), last_play.get(a)
                                    if le and -900 < le[0] - r["t"] < 15:
                                        why = miss_cause(le[1])
                                    elif lp and -3600 < lp - r["t"] < 15:    # the account has been watching
                                        why = "own playback"
                                    elif le and le[0] - r["t"] <= -900:
                                        why = miss_cause(le[1], r["t"] - le[0])
                                    else:
                                        why = miss_cause(None)
                                hits.append((ts, a, why, int(d.group(5)), r["miss"], r["url"],
                                             describe(r["url"], "", "x", self.sections)[0],
                                             sorted(r["rows"], key=lambda x: -x[1])[:4]))
                            if ts and ts > last_ts:
                                dev = self.device_at(acct, r["t"]) if acct and r else None
                                loads.append((ts, int(d.group(5)), acct,
                                              ("cid:" + dev) if dev else
                                              (st.device_for(acct, d.group(1)) if acct else "unknown"),
                                              describe(d.group(3), "", "x", self.sections)[0]))
                                newest = max(newest, ts)
                        continue
                    if "HubCache: " not in line:
                        continue
                    h = HITRATIO.search(line)
                    if h:
                        hit = (int(h.group(1)), float(h.group(2)))
                        continue
                    m = HUBKEY.search(line)
                    if not m:
                        x = EXPIRE.search(line)
                        if x:
                            tm = TS.match(line)
                            when, event = _epoch(tm.group(1)) if tm else 0, (x.group(2) or "").rstrip(".")
                            last_exp[x.group(1)] = (when, event)
                            if "play" in event:
                                last_play[x.group(1)] = when
                        continue
                    hub_lines += 1
                    rid, kind, key, took = m.groups()
                    if kind == "Computed" and took:          # what each row costs to build (our replays included)
                        c = rows.setdefault(key.split("/")[1], [0, 0, 0])
                        c[0], c[1], c[2] = c[0] + 1, c[1] + int(took), max(c[2], int(took))
                    if rid in own:
                        continue
                    p = parse_key(key)
                    r = real.get(rid)
                    if r and kind != "Adding":
                        r["hit" if kind == "Retrieving" else "miss"] += 1
                        if kind == "Computed" and took:
                            r["rows"].append([key.split("/")[1], int(took)])
                        r["acct"] = r["acct"] or key.split("/", 1)[0]
                        if p and r["known"] is None:         # judged once, on the request's first row
                            e = json.dumps(p[1:])
                            r["known"] = e in known_before.get(p[0], ()) or e in seen.get(p[0], ())
                    if p:
                        e = json.dumps(p[1:])
                        seen.setdefault(p[0], {})[e] = req_ip.get(rid)
                        if r and e not in seen_dev.get(p[0], ()):
                            dev = self.device_at(p[0], r["t"])
                            if dev:
                                seen_dev.setdefault(p[0], {})[e] = dev
        st.merge_learned(seen, self.S.get("keep_days"), self.S.get("max_requests_per_account"), drop_ips=self_ips,
                         devices=seen_dev)
        st.note_activity(events, newest_act)
        st.note_photo_profiles(photo_seen, self.S.get("keep_days"))
        st.add_photo_loads(photo_loads)
        st.add_hits(hits, int(self.S.get("slow_load_seconds") * 1000))
        st.set_rows(rows)
        st.add_loads(loads, newest)
        st.note_logs(hub_lines, hit)
        if hub_lines == 0:
            log("WARNING: no HubCache lines in the Plex logs - turn on debug logging in Plex "
                "(Settings > General > Enable Plex Media Server debug logging).")
        return hub_lines

    # ---------- smart mode ----------
    def usual_hours(self, acct):
        """True if this account normally uses Plex in this hour or the next one (hour of the week)."""
        a = self.store.stats.get("activity", {}).get(acct)
        if not a:
            return False
        now = datetime.datetime.now()
        h = now.weekday() * 24 + now.hour
        return a["how"][h] + a["how"][(h + 1) % 168] > 0

    def recently_active(self, acct):
        a = self.store.stats.get("activity", {}).get(acct)
        return bool(a) and time.time() - a.get("last", 0) < self.S.get("smart_recent_hours") * 3600

    def on_playing_device(self, acct, e):
        """Was this request learned from the device the account is (or was just) playing on? Uses the Plex
        device when it is known, the address otherwise."""
        key = self.store.entry_device(acct, e)
        if key.startswith("cid:"):
            return key in self.play_devs.get(acct, ())
        return self.store.entry_ip(acct, e) in self.play_ips.get(acct, ())

    def plan(self, only_user=None, collections=False):
        """-> ({acct: [entries, most recently used first]} to warm now, {acct: reason}, {acct: priority}).
        Priority 0 = playback just ended, 1 = active recently, 2 = usual hours / always, 3 = idle refresh.
        collections: only the requests that show collection rows, only for accounts likely to open Plex now
        (playback stopped somewhere, which drops every account's collection rows)."""
        st, S = self.store, self.S
        off_a, off_d = S.disabled("accounts"), S.disabled("devices")
        names = st.stats.get("account_names", {})
        smart = S.get("mode") == "smart"
        now = time.time()
        todo, why, prio = {}, {}, {}
        self.partial = set()
        skip_playing = S.get("skip_while_playing") and not only_user
        for acct, ent in sorted(st.learned.get("accounts", {}).items()):
            name = names.get(acct, acct)
            ids = {name, acct}
            if only_user and only_user not in ids:
                continue
            if acct in off_a or (C.INCLUDE and not C.INCLUDE & ids) or C.EXCLUDE & ids:
                why[acct] = "off"
                continue
            watching = bool(skip_playing and acct in self.playing)
            if watching:
                # Only the device that is playing is left alone. People browse on a second device (or share
                # the account) while something plays; Plex drops the account's rows for the library being
                # watched a few times an hour, and those other devices should find them rebuilt.
                why[acct], prio[acct] = "watching now (other devices kept warm)", 1
            elif skip_playing and acct in self.after_play:
                why[acct], prio[acct] = "playback just ended", 0
            elif smart and not only_user:
                if self.recently_active(acct):
                    why[acct], prio[acct] = "active recently", 1
                elif self.usual_hours(acct):
                    why[acct], prio[acct] = "usual hours", 2
                elif now - st.learned.get("acct_last_warm", {}).get(acct, 0) >= S.get("smart_idle_minutes") * 60:
                    why[acct], prio[acct] = "out of hours (idle refresh)", 3
                elif S.get("home_always"):           # Home is what everyone sees first: keep it ready for all
                    why[acct], prio[acct] = "out of hours (home only)", 3
                    self.partial.add(acct)
                else:
                    why[acct] = "out of hours (waiting)"
                    continue
            else:
                why[acct], prio[acct] = "always", 1 if self.recently_active(acct) else 2
            es = [e for e in sorted(ent, key=ent.get, reverse=True)
                  if f"{acct}|{st.entry_device(acct, e)}" not in off_d]
            if watching:
                # Plex drops the rows of the library being watched within seconds of every progress update,
                # on all of the account's devices: rebuilding a request that only covers that library is
                # wasted work. Everything else (other libraries, the combined Home) is worth keeping warm.
                watched = self.play_secs.get(acct, set())
                es = [e for e in es if not self.on_playing_device(acct, e)
                      and not (entry_sections(e) or {None}) <= watched]
                self.partial.discard(acct)
            if acct in self.partial:
                es = [e for e in es if is_home_entry(e)]
            if collections:
                es = [e for e in es if has_collection_rows(e)] if prio.get(acct, 3) < 3 else []
            if prio.get(acct) == 0:                  # the device they were just watching on is back on Home first
                es.sort(key=lambda e: not self.on_playing_device(acct, e))
            if es:
                todo[acct] = es
            elif watching:
                why[acct] = "watching now (warms when playback ends)"
                prio.pop(acct, None)
            elif acct in self.partial:
                why[acct] = "out of hours (waiting)"
                prio.pop(acct, None)
            elif collections:
                prio.pop(acct, None)
            elif ent:
                why[acct] = "all devices off"
        if collections:
            self.partial.update(todo)            # a part of the account's screens: not a full warm of the account
        return todo, why, prio

    # ---------- one pass ----------
    def run_once(self, force=False, only_user=None, dry_run=False, label="", collections=False):
        otoken = self.owner_token()
        if not otoken or not self.S.get("plex_url"):     # nothing to do until the page has both (see web.py)
            self.state = "waiting-for-setup"
            return
        st, S = self.store, self.S
        try:   # marked request: lets learn() find the address Plex sees us as (works on any host / k8s node)
            self.pms(f"/identity?hubwarmer_self={st.client_id}", otoken, timeout=10)
        except Exception:
            pass
        self.poll_bandwidth(otoken)
        self.learn(otoken)
        n_tc = self.update_sessions(otoken)
        probe_s = 0.0
        if "1" not in self.playing or not S.get("skip_while_playing"):   # the owner's own playback drops the
            probe = "/hubs/promoted?contentDirectoryID=1&count=20&excludeContinueWatching=1"   # probe's rows
            probe_s, _ = self.pms(probe, otoken)
            st.note_probe(probe_s)
        due = time.time() - st.learned.get("last_warm", 0) > S.get("top_up_minutes") * 60
        todo, why, prio = self.plan(only_user, collections=collections)
        if not collections:
            self.last_plan = {"ts": time.time(), "why": why,
                              "requests": sum(len(v) for v in todo.values()), "accounts": len(todo)}
        full = not only_user and not collections
        if collections and not todo:
            self.state = "idle"
            return
        if not collections and not (force or due or probe_s > S.get("probe_slow_seconds")):
            todo = {a: es for a, es in todo.items() if prio.get(a) == 0}   # only who just stopped watching
            full = False
            if not todo:
                self.state = "idle"
                return
        if S.get("pause_above_transcodes") and n_tc > S.get("pause_above_transcodes"):
            log(f"skipping: {n_tc} transcodes > {S.get('pause_above_transcodes')}")
            self.state = "idle"
            return
        self._warm(todo, prio, otoken, dry_run=dry_run, full=full, no_budget=bool(only_user), label=label,
                   kind="playback" if collections else "library" if label else "")
        self.state = "idle"

    def _warm(self, todo, prio, otoken, dry_run=False, full=True, no_budget=False, label="", kind=""):
        """Replay todo. Most urgent accounts first (then whoever waited longest); stops starting new accounts
        once the time budget is used - the rest go first on the next pass."""
        st, S = self.store, self.S
        self.state = "warming"
        toks = self.account_tokens(otoken)
        budget = 0 if no_budget else S.get("pass_budget_seconds")
        last_warm = st.learned.get("acct_last_warm", {})
        t0, n, cold, cold_s, errors, per_acct, done, left = time.time(), 0, 0, 0.0, 0, {}, [], 0
        for acct in sorted(todo, key=lambda a: (prio.get(a, 2), last_warm.get(a, 0))):
            entries = todo[acct]
            name, tok = toks.get(acct, (acct, None))
            if not tok:
                self.after_play.pop(acct, None)
                continue
            if budget and prio.get(acct, 2) > 0 and time.time() - t0 > budget:
                left += len(entries)
                continue
            for e in entries:
                url, lang, feat = json.loads(e)
                if dry_run:
                    log(f"would warm {name} [{lang or '-'}|{feat or '-'}] {url[:90]}")
                    continue
                try:
                    s, body = get(self.S.get("plex_url") + url, tok, lang=lang, feat=feat, client_id=st.client_id)
                except (urllib.error.URLError, OSError) as ex:
                    errors += 1
                    log(f"{name} error {ex} {url[:80]}")
                    continue
                n += 1
                was_cold = s > C.COLD_S
                cold += was_cold
                cold_s += s if was_cold else 0
                per_acct.setdefault(acct, {})[e] = (round(s, 3), was_cold)
                if S.get("warm_posters"):
                    self.queue_posters(acct, hub_thumbs(body))
                if was_cold:
                    log(f"{name} {s:5.2f}s [{lang or '-'}|{feat[:30] or '-'}] {url[:80]}")
            done.append(acct)
            if kind != "playback":           # a collection pass leaves out Continue Watching: not their full warm
                self.after_play.pop(acct, None)
        took = time.time() - t0
        log(f"warmed {n} requests for {len(done)} accounts ({cold} were cold, {errors} errors) in {took:.1f}s"
            + (f"; time budget reached, {left} requests wait for the next pass" if left else "")
            + (f" [{label}]" if label else "" if full else " [after playback]"))
        self.last_pass_end = time.time()
        if not dry_run:
            st.note_run(n, cold, cold_s, errors, took, per_acct, [a for a in done if a not in self.partial],
                        full=full, n_accounts=len(done), kind=kind or ("" if full else "after playback"))
            if S.get("warm_posters"):
                self.warm_posters(toks)

    # ---------- posters ----------
    def poster_identity(self, acct, profile, thumb):
        """what Plex's image cache keys on: with a token in the URL the image is cached per user"""
        return (acct if json.loads(profile)["token"] else "", profile, thumb)

    def queue_posters(self, acct, thumbs):
        """Queue the posters of a warmed screen, in every form this account's apps ask for them."""
        profiles = self.store.stats.get("photo_profiles", {}).get(acct, {})
        if acct == "1":      # the owner's apps use their own device tokens, which we don't have
            profiles = {p: t for p, t in profiles.items() if not json.loads(p)["token"]}
        now = time.time()
        for profile in profiles:
            for thumb in thumbs:
                ident = self.poster_identity(acct, profile, thumb)
                if now - self.photo_done.get(ident, 0) > 12 * 3600:
                    self.photo_done[ident] = now
                    self.photo_queue.append((acct, profile, thumb))

    def warm_posters(self, toks):
        """Ask Plex for queued posters so the resized image is already in its cache when an app wants it.
        One at a time, within its own time budget; the rest stay queued for the next pass."""
        if not self.photo_queue:
            return
        budget, t0 = self.S.get("poster_budget_seconds"), time.time()

        def fetch(job):
            acct, profile, thumb = job
            tok = toks.get(acct, (None, None))[1]
            if not tok:
                return None
            p = json.loads(profile)
            target = (p["host"] + thumb + "?X-Plex-Token=" + tok) if p["token"] else (p["host"] + thumb)
            query = urllib.parse.urlencode([tuple(kv) for kv in p["params"]] + [("url", target)])
            try:
                return get(f"{self.S.get('plex_url')}/photo/:/transcode?{query}", tok, timeout=60, accept="image/*",
                           client_id=self.store.client_id)[0]
            except (urllib.error.URLError, OSError):
                return -1

        n = built = errors = 0
        built_s = 0.0
        with concurrent.futures.ThreadPoolExecutor(max_workers=POSTER_WORKERS) as pool:
            while self.photo_queue and time.time() - t0 < budget:
                batch, self.photo_queue = self.photo_queue[:POSTER_WORKERS * 4], self.photo_queue[POSTER_WORKERS * 4:]
                for s in pool.map(fetch, batch):
                    if s is None:
                        continue
                    if s < 0:
                        errors += 1
                        continue
                    n += 1
                    if s > 0.08:
                        built += 1
                        built_s += s
        if len(self.photo_done) > 200000:
            cutoff = time.time() - 12 * 3600
            self.photo_done = {k: t for k, t in self.photo_done.items() if t > cutoff}
        self.store.note_posters(n, built, built_s, errors, len(self.photo_queue))
        if built or errors:
            log(f"posters: {n} requested, {built} prepared in {built_s:.0f}s, {errors} errors, "
                f"{len(self.photo_queue)} queued")

    def tick(self):
        """Between checks: watch playback sessions; warm an account as soon as it stops watching."""
        otoken = self.owner_token()
        if not otoken or not self.S.get("plex_url") or not self.S.get("skip_while_playing"):
            return
        self.update_sessions(otoken)
        self.poll_bandwidth(otoken)
        if not self.after_play:
            return
        todo, _, prio = self.plan()
        todo = {a: es for a, es in todo.items() if prio.get(a) == 0}
        for a in list(self.after_play):
            if a not in todo:
                self.after_play.pop(a)
        if todo:
            self._warm(todo, prio, otoken, full=False)
            self.state = "idle"

    def loop(self, stop):
        log(f"hub-warmer {__version__}: Plex {self.S.get('plex_url') or '(not set: open the web page)'}, "
            f"logs {self.S.get('plex_logs')}, data {C.DATA_DIR}")
        self.events.start(stop)
        label, collections = "", False
        while not stop.is_set():
            force, self.force_next = self.force_next or bool(label), False
            try:
                self.run_once(force=force, label=label, collections=collections)
                self.last_error = ""
            except Exception as e:
                self.state, self.last_error = "error", repr(e)
                log(f"run failed: {e!r}")
            if collections:
                self.events.done_play()
                wait = max(1.0, self.next_check - time.time())   # a collection pass leaves the schedule as it was
            else:
                wait = self.S.get("interval_seconds")
            label, collections = "", False
            self.next_check = time.time() + wait
            while not stop.is_set():
                if self.events.due(self.last_pass_end):        # the library changed: warm now, not at the next check
                    label = self.events.take()
                    break
                if self.events.play_due(self.last_pass_end):   # playback stopped: collection rows only
                    label, collections = self.events.take_play(), True
                    break
                left = self.next_check - time.time()
                step = min(left, self.S.get("session_poll_seconds"),
                           5 if self.events.pending or self.events.play_pending else 3600)
                if left <= 0 or self.wake.wait(step):
                    break
                try:
                    self.tick()
                except Exception as e:
                    log(f"session check failed: {e!r}")
            self.wake.clear()

    def warm_now(self):
        self.force_next = True
        self.wake.set()
