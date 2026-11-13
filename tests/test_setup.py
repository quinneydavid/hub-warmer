"""First-run setup on the page, and the settings that used to be environment-only."""
import json, os, sys, tempfile, threading, unittest, urllib.error, urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())

from hubwarmer import auth, config as C, settings, web  # noqa: E402
from hubwarmer.core import Warmer  # noqa: E402
from hubwarmer.store import Store  # noqa: E402

GOOD = "http://plex.test:32400"


def fake_probe(url):
    if url.rstrip("/") != GOOD:
        raise ValueError("no answer from that address (timed out)")
    return "machine-1", "1.41.0"


class Page(unittest.TestCase):
    def setUp(self):
        self.patches = [mock.patch.object(settings, "probe_plex", fake_probe), mock.patch.object(C, "WEB_AUTH", "plex"),
                        mock.patch.object(C, "PLEX_TOKEN", ""), mock.patch.object(C, "PLEX_PREFS", "/nonexistent")]
        for p in self.patches:
            p.start()
        self.store = Store(tempfile.mkdtemp())
        self.warmer = Warmer(self.store)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(web.App(self.store, self.warmer)))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        for p in self.patches:
            p.stop()

    def call(self, path, body=None, cookie=None, header=True):
        h = {"Content-Type": "application/json"}
        if header:
            h["X-Hub-Warmer"] = "1"
        if cookie:
            h["Cookie"] = cookie
        req = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(), headers=h)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def session(self, role):
        user = {"id": "7", "username": "alice", "title": "Alice", "thumb": ""}
        return f"{auth.COOKIE}={auth.make_session(self.store.config['secret'], user, role)}"

    def get(self, path, cookie=None):
        """One GET without following redirects -> (status, Location, Set-Cookie)"""
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        req = urllib.request.Request(self.base + path, headers={"Cookie": cookie} if cookie else {})
        try:
            with urllib.request.build_opener(NoRedirect).open(req, timeout=5) as r:
                return r.status, r.headers.get("Location", ""), r.headers.get("Set-Cookie", "")
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Location", ""), e.headers.get("Set-Cookie", "")

    def test_sign_in_belongs_to_the_browser_and_the_address_shown(self):
        user = {"id": "7", "username": "alice", "title": "Alice", "thumb": ""}
        pins = iter(range(100, 200))
        with mock.patch.object(auth, "start_pin", lambda cid: (next(pins), "code")), \
             mock.patch.object(auth, "check_pin", lambda cid, pin: "owner-token"), \
             mock.patch.object(auth, "plextv_user", lambda cid, tok: user), \
             mock.patch.object(auth, "owns_server", lambda cid, tok, machine: machine == "machine-1"), \
             mock.patch.object(self.warmer, "warm_now", lambda: None):
            self.call("/api/setup", {"plex_url": GOOD})
            self.assertEqual(self.call("/api/status")[1]["plex_url"], GOOD)      # shown on the sign-in step
            # another browser (no cookie) cannot finish a sign-in someone else started
            code, loc, setc = self.get("/login")
            self.assertEqual(code, 302)
            nonce = setc.split(";")[0]
            self.assertTrue(nonce.startswith(web.PIN_COOKIE + "=") and "HttpOnly" in setc)
            self.assertEqual(self.get("/auth/callback?pin=100")[1], "/?e=expired")
            self.assertEqual(self.store.config.get("owner_token"), None)
            # the address changes while the owner is signing in: refused, nothing stored
            _, _, setc = self.get("/login")
            with mock.patch.object(settings, "probe_plex", lambda url: ("machine-1", "1.41.0")):
                self.call("/api/setup", {"plex_url": "http://other.test:32400"})
                self.assertEqual(self.get("/auth/callback?pin=101", setc.split(";")[0])[1], "/?e=changed")
                self.assertEqual(self.store.config.get("owner_token"), None)
            # same browser, same address: setup completes and the session carries the Settings lifetime
            self.call("/api/setup", {"plex_url": GOOD})
            _, _, setc = self.get("/login")
            code, loc, session = self.get("/auth/callback?pin=102", setc.split(";")[0])
            self.assertEqual((code, loc), (302, "/"))
            self.assertEqual(self.store.config["owner_token"], "owner-token")
            self.assertIn("Max-Age=2592000", session)

    def test_session_lifetime_follows_the_setting(self):
        import base64, time
        user = {"id": "7", "username": "alice", "title": "Alice", "thumb": ""}
        c = auth.make_session("s3cret", user, "owner", days=2)
        exp = json.loads(base64.urlsafe_b64decode(c.split(".")[0]))["exp"]
        self.assertAlmostEqual(exp - time.time(), 2 * 86400, delta=5)

    def test_sign_ins_under_way_are_limited(self):
        with mock.patch.object(auth, "start_pin", lambda cid: (id(object()), "code")), mock.patch.object(web, "MAX_PINS", 3):
            self.assertEqual([self.get("/login")[1].startswith("https://app.plex.tv/") for _ in range(4)],
                             [True, True, True, False])

    def test_nothing_configured_waits(self):
        self.warmer.run_once()                                   # no address, no token: no error, no request
        self.assertEqual(self.warmer.state, "waiting-for-setup")
        self.warmer.tick()
        code, j = self.call("/api/status")
        self.assertEqual((code, j["setup_needed"], j["need_address"]), (401, True, True))

    def test_address_is_tested_and_stored(self):
        code, j = self.call("/api/setup", {"plex_url": "http://wrong.test:32400"})
        self.assertEqual(code, 400)
        self.assertIn("no answer", j["errors"][0])
        self.assertEqual(self.warmer.S.get("plex_url"), "")
        code, j = self.call("/api/setup", {"plex_url": "ftp://plex.test"})
        self.assertEqual(code, 400)
        code, j = self.call("/api/setup", {"plex_url": GOOD + "/"})
        self.assertEqual((code, j["version"]), (200, "1.41.0"))
        self.assertEqual(self.warmer.S.get("plex_url"), GOOD)    # used at once, no restart
        with open(self.store.p_config) as f:
            self.assertEqual(json.load(f)["settings"]["plex_url"], GOOD)
        self.assertEqual(self.call("/api/status")[1]["need_address"], False)   # next: Sign in with Plex
        self.assertEqual(self.call("/api/setup", {"plex_url": GOOD}, header=False)[0], 403)   # only from the page

    def test_form_closed_after_setup(self):
        self.call("/api/setup", {"plex_url": GOOD})
        self.store.config["owner_token"] = "owner-token"         # what the first owner sign-in stores
        self.assertEqual(self.call("/api/setup", {"plex_url": GOOD})[0], 403)
        self.assertEqual(self.call("/api/setup", {"plex_url": GOOD}, cookie=self.session("friend"))[0], 403)
        code, j = self.call("/api/status")
        self.assertEqual((code, j["setup_needed"], j["need_address"]), (401, False, False))

    def test_owner_changes_address_in_settings(self):
        self.call("/api/setup", {"plex_url": GOOD})
        self.store.config["owner_token"] = "owner-token"
        owner, friend = self.session("owner"), self.session("friend")
        self.assertEqual(self.call("/api/settings", {"changes": {"plex_url": GOOD}}, cookie=friend)[0], 403)
        self.assertEqual(self.call("/api/settings", {"changes": {"plex_url": GOOD}})[0], 401)
        code, j = self.call("/api/settings", {"changes": {"plex_url": "http://wrong.test:32400"}}, cookie=owner)
        self.assertEqual(code, 400)
        self.assertIn("Plex Media Server address", j["errors"][0])
        self.assertEqual(self.warmer.S.get("plex_url"), GOOD)
        self.warmer.machine_id = "old"
        self.assertEqual(self.call("/api/settings", {"changes": {"plex_url": GOOD}}, cookie=owner)[0], 200)
        self.assertEqual(self.warmer.machine_id, "")

    def test_metrics_switch(self):
        self.assertEqual(urllib.request.urlopen(self.base + "/metrics", timeout=5).status, 200)
        self.assertEqual(self.warmer.S.update({"metrics_public": "0"}), [])
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(self.base + "/metrics", timeout=5)
        self.assertEqual(e.exception.code, 401)


class MovedSettings(unittest.TestCase):
    def setUp(self):
        self.store = Store(tempfile.mkdtemp())
        self.S = settings.Settings(self.store)

    def test_defaults_and_validation(self):
        self.assertEqual((self.S.get("history_days"), self.S.get("metrics_public")), (7.0, 1))
        self.assertEqual(self.S.self_ips(), {"127.0.0.1"})
        self.assertEqual(self.S.update({"self_ips": " 10.0.0.5, 10.0.0.6 "}), [])
        self.assertEqual(self.S.self_ips(), {"127.0.0.1", "10.0.0.5", "10.0.0.6"})
        self.assertTrue(self.S.update({"plex_logs": "/no/such/folder"}))
        logs = tempfile.mkdtemp()
        self.assertEqual(self.S.update({"plex_logs": logs}), [])
        self.assertEqual(self.S.get("plex_logs"), logs)
        self.assertTrue(self.S.update({"public_url": "warmer.example.com"}))
        self.assertEqual(self.S.update({"public_url": "https://warmer.example.com/"}), [])
        self.assertEqual(self.S.get("public_url"), "https://warmer.example.com")
        self.assertEqual(self.S.update({"public_url": ""}), [])              # empty = back to the default
        self.assertEqual(self.S.get("public_url"), "")
        self.assertTrue(self.S.update({"history_days": 0}))
        self.assertTrue(self.S.update({"session_days": 1000}))

    def test_history_days_prunes(self):
        import time
        self.store.stats["runs"] = [{"ts": time.time() - 3 * 86400}, {"ts": time.time() - 60}]
        self.assertEqual(self.S.update({"history_days": 2}), [])
        self.store._prune()
        self.assertEqual(len(self.store.stats["runs"]), 1)

    def test_probe_rejects_bad_scheme(self):
        with self.assertRaises(ValueError):
            settings.probe_plex("file:///etc/passwd")


if __name__ == "__main__":
    unittest.main()
