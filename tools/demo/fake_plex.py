"""A fake Plex Media Server for the demo. It answers the requests Hub Warmer makes to Plex, and the handful it
makes to plex.tv (run.py sends both here), from the invented data in demo_data.py. Standard library only."""
import base64, io, json, random, threading, time, urllib.parse, zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import demo_data as D

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=")


class FakePlex:
    def __init__(self, now=None, seed=11):
        self.log = D.history(now or time.time(), seed)
        self.extra = []                      # log lines written while running (Hub Warmer's marked request)
        self.cache = {}                      # (token, request, language, features) -> cached until
        self.posters = set()                 # resized posters "in the image cache"
        self.rng = random.Random(seed + 2)
        self.lock = threading.Lock()

    def hub_seconds(self, key, rows):
        """How long this request takes: a few ms from the cache, else the build time of its rows. An entry
        stays cached for a while and is then dropped, as a busy library would."""
        now = time.time()
        with self.lock:
            cached = self.cache[key] > now if key in self.cache else self.rng.random() < 0.7
            self.cache[key] = now + self.rng.uniform(15, 240)
            if cached:
                return self.rng.uniform(0.02, 0.07)
            some = rows if self.rng.random() < 0.5 else self.rng.sample(rows, max(1, len(rows) // 2))
            return sum(D.ROW_MS[h] for h in some) / 1000 * self.rng.uniform(0.8, 1.2)

    def poster_seconds(self, key):
        with self.lock:
            fresh = key not in self.posters
            self.posters.add(key)
            return self.rng.uniform(0.09, 0.2) if fresh and self.rng.random() < 0.3 else 0.004

    def logs_zip(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            with self.lock:
                z.writestr("Plex Media Server/Logs/Plex Media Server.log", self.log + "".join(self.extra))
        return buf.getvalue()

    def serve(self, port=0):
        """-> the running server (its port is server_address[1])"""
        srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(self))
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv


def make_handler(fake):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, body, ctype="application/json", code=200):
            data = body if isinstance(body, bytes) else (body if isinstance(body, str) else json.dumps(body)).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def container(self, **kw):
            self.send({"MediaContainer": kw})

        def do_POST(self):
            if self.path == "/api/v2/pins":                          # plex.tv: start a sign-in
                return self.send({"id": 4242, "code": "demo"}, code=201)
            self.send("not found", "text/plain", 404)

        def do_GET(self):
            path, _, query = self.path.partition("?")
            if path == "/identity":
                if "hubwarmer_self=" in query:                        # Plex would log this request: so do we
                    with fake.lock:
                        fake.extra.append(f"{D._stamp(time.time())}Request: [{D.WARMER_IP}:40000 (WAN)] GET {self.path} "
                                          f"(2 live) #{0xf000 + len(fake.extra):x} Signed-in Token ({D.OWNER['name']})\n")
                return self.container(machineIdentifier=D.MACHINE, version=D.VERSION)
            if path == "/api/v2/pins/4242":                          # plex.tv: the demo sign-in is always approved
                return self.send({"id": 4242, "authToken": D.OWNER["token"]})
            acct = D.BY_TOKEN.get(self.headers.get("X-Plex-Token"))
            if not acct:
                return self.send("unauthorized", "text/plain", 401)
            # ---- plex.tv ----
            if path == "/api/v2/user":
                return self.send({"id": int(acct["plextv"]), "username": acct["name"], "title": acct["title"], "thumb": ""})
            if path == "/api/v2/resources":
                return self.send([{"name": "Living Room Plex", "clientIdentifier": D.MACHINE, "owned": True}])
            if path == f"/api/servers/{D.MACHINE}/shared_servers":
                return self.send("<MediaContainer>" + "".join(
                    f'<SharedServer userID="{a["id"]}" username="{a["name"]}" accessToken="{a["token"]}"/>'
                    for a in D.ACCOUNTS[1:]) + "</MediaContainer>", "application/xml")
            # ---- Plex Media Server ----
            if path == "/accounts":
                return self.container(Account=[{"id": 0, "name": ""}] + [{"id": int(a["id"]), "name": a["name"]} for a in D.ACCOUNTS])
            if path == "/library/sections":
                return self.container(Directory=[{"key": k, "title": t, "type": typ} for k, (t, typ) in D.LIBRARIES.items()])
            if path.startswith("/library/sections/") and path.endswith("/collections"):
                colls = D.COLLECTIONS.get(path.split("/")[3], {})
                return self.container(Metadata=[{"ratingKey": k, "title": t} for k, t in colls.items()])
            if path == "/status/sessions":
                who, cid, section = D.PLAYING
                dev = next(d for a in D.ACCOUNTS for d in a["devices"] if d["cid"] == cid)
                return self.container(Metadata=[{"type": "episode", "librarySectionID": section, "User": {"id": who}, "Player": {
                    "address": dev["ip"], "machineIdentifier": cid, "title": dev["title"], "product": dev["product"],
                    "platform": dev["platform"], "state": "playing"}}])
            if path == "/statistics/bandwidth":
                return self.container(Device=[], StatisticsBandwidth=[])
            if path == "/diagnostics/logs":
                return self.send(fake.logs_zip(), "application/zip")
            if path == "/photo/:/transcode":
                time.sleep(fake.poster_seconds((acct["id"] if "X-Plex-Token" in urllib.parse.unquote(query) else "", query)))
                return self.send(PNG, "image/png")
            if path.startswith("/hubs/"):
                rows, feat = D.BY_URL.get(self.path, []), self.headers.get("X-Plex-Features", "")
                time.sleep(0.03)
                if feat and rows:                                     # a replayed client request (the probe sends no features)
                    time.sleep(fake.hub_seconds((acct["id"], self.path, self.headers.get("X-Plex-Language", ""), feat), rows))
                return self.container(size=len(rows), Hub=[
                    {"hubIdentifier": h, "Metadata": [{"ratingKey": t.split("/")[3], "thumb": t} for t in D.THUMBS[h]]}
                    for h in rows])
            self.send("not found", "text/plain", 404)

    return H
