"""End-to-end learning from a synthetic Plex log folder (no network: server calls are stubbed)."""
import json, time, os, sys, tempfile, unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
LOGDIR = tempfile.mkdtemp()
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["PLEX_LOGS"] = LOGDIR

from hubwarmer import config as C  # noqa: E402
from hubwarmer.core import Warmer  # noqa: E402
from hubwarmer.store import Store  # noqa: E402

C.PLEX_LOGS = LOGDIR

LOG = """\
Nov 09, 2026 16:00:00.000 [1] DEBUG - Request: [10.0.0.5:4000 (WAN)] GET /identity?hubwarmer_self={cid} (3 live) #a1 Signed-in Token (owner)
Nov 09, 2026 16:00:01.000 [1] DEBUG - Request: [192.168.1.20:5000 (WAN)] GET /hubs/sections/1?count=12 (3 live) #b1 GZIP Signed-in Token (alice)
Nov 09, 2026 16:00:01.100 [1] DEBUG - [Req#b1] HubCache: Computed '42/custom.collection.1.7/1/hubs/sections/1/en-GBexternal-media,indirect-media/count=12' in 400 ms.
Nov 09, 2026 16:00:01.200 [1] DEBUG - [Req#b1] HubCache: 100 hubs cached, 61.5% hit ratio.
Nov 09, 2026 16:00:01.500 [1] DEBUG - Completed: [192.168.1.20:5000] 200 GET /hubs/sections/1?count=12 (3 live) #b1 TLS GZIP 1500ms 999 bytes
Nov 09, 2026 16:00:02.000 [1] DEBUG - Request: [10.0.0.5:4001 (WAN)] GET /hubs/promoted?contentDirectoryID=1 (3 live) #c1 Signed-in Token (alice)
Nov 09, 2026 16:00:02.100 [1] DEBUG - [Req#c1] HubCache: Computed '42/home.ondeck/1/hubs/promoted/en-GBexternal-media,indirect-media/contentDirectoryID=1' in 90 ms.
Nov 09, 2026 16:00:02.200 [1] DEBUG - Completed: [10.0.0.5:4001] 200 GET /hubs/promoted?contentDirectoryID=1 (3 live) #c1 90ms 999 bytes
Nov 09, 2026 16:00:03.000 [1] DEBUG - Request: [192.168.1.20:5000 (WAN)] GET /hubs/sections/1/manage (3 live) #d1 Signed-in Token (alice)
Nov 09, 2026 16:01:00.000 [1] DEBUG - [Req#e0] HubCache: Expiring '42/custom.collection.1.7/1/hubs/sections/1/en-GBexternal-media,indirect-media/count=12' because of event plex.event.play.session.state.update.
Nov 09, 2026 16:01:02.000 [1] DEBUG - Request: [192.168.1.20:5000 (WAN)] GET /hubs/sections/1?count=12 (3 live) #e1 GZIP Signed-in Token (alice)
Nov 09, 2026 16:01:02.100 [1] DEBUG - [Req#e1] HubCache: Retrieving '42/movie.recentlyadded.1/1/hubs/sections/1/en-GBexternal-media,indirect-media/count=12' from the cache.
Nov 09, 2026 16:01:02.110 [1] DEBUG - [Req#e1] HubCache: Adding '42/custom.collection.1.7/1/hubs/sections/1/en-GBexternal-media,indirect-media/count=12).
Nov 09, 2026 16:01:02.400 [1] DEBUG - [Req#e1] HubCache: Computed '42/custom.collection.1.7/1/hubs/sections/1/en-GBexternal-media,indirect-media/count=12' in 300 ms.
Nov 09, 2026 16:01:02.500 [1] DEBUG - Completed: [192.168.1.20:5000] 200 GET /hubs/sections/1?count=12 (3 live) #e1 TLS GZIP 480ms 999 bytes
Nov 09, 2026 16:02:02.000 [1] DEBUG - Request: [192.168.1.20:5000 (WAN)] GET /hubs/sections/1?count=12 (3 live) #f1 GZIP Signed-in Token (alice)
Nov 09, 2026 16:02:02.100 [1] DEBUG - [Req#f1] HubCache: Retrieving '42/custom.collection.1.7/1/hubs/sections/1/en-GBexternal-media,indirect-media/count=12' from the cache.
Nov 09, 2026 16:02:02.200 [1] DEBUG - Completed: [192.168.1.20:5000] 200 GET /hubs/sections/1?count=12 (3 live) #f1 TLS GZIP 40ms 999 bytes
"""


class Learn(unittest.TestCase):
    def setUp(self):
        self.store = Store(os.environ["DATA_DIR"])
        with open(os.path.join(LOGDIR, "Plex Media Server.log"), "w") as f:
            f.write(LOG.format(cid=self.store.client_id))
        self.store.set_accounts({"1": "owner", "42": "alice"}, {"1": "100", "42": "42"})
        self.w = Warmer(self.store)

    def test_learns_real_requests_and_skips_own(self):
        with mock.patch.object(Warmer, "refresh_server_info"):
            lines = self.w.learn("tok", force=True)
        self.assertEqual(lines, 5)
        acc = self.store.learned["accounts"]
        self.assertEqual(list(acc), ["42"])                       # own replay (#c1 from 10.0.0.5) not learned
        (entry,) = acc["42"]
        self.assertEqual(json.loads(entry), ["/hubs/sections/1?count=12", "en-GB", "external-media,indirect-media"])
        self.assertEqual(self.store.entry_ip("42", entry), "192.168.1.20")
        self.assertIn("10.0.0.5", self.store.stats["self_ips"])   # found via the marked /identity request
        self.assertEqual(self.store.stats["plex_hit_ratio"], 61.5)

    def test_real_loads_and_usage_hours(self):
        with mock.patch.object(Warmer, "refresh_server_info"):
            self.w.learn("tok", force=True)
        lt = self.store.stats["loads_total"]
        self.assertEqual((lt["count"], lt["sum_ms"]), (3, 2020))  # only the real user's loads, not our own
        self.assertEqual(sum(self.store.stats["activity"]["42"]["how"]), 3)
        self.assertIn("42", self.store.stats["loads_acct"])

    def test_real_hits_slow_loads_causes_and_row_costs(self):
        with mock.patch.object(Warmer, "refresh_server_info"):
            self.w.learn("tok", force=True)
        ht = self.store.stats["hits_total"]
        # b1 (1500 ms): first time seen, slow; e1 (480 ms): rebuilt after alice's own playback but under the 1 s
        # threshold, so not a slow load; f1 (40 ms): straight from the cache
        self.assertEqual((ht["n"], ht["hit"], ht["slow"]), (3, 1, 1))
        self.assertEqual(ht["c"], {"new app or screen": 1})
        self.assertEqual([(m["why"], m["ms"]) for m in self.store.stats["misses"]], [("new app or screen", 1500)])
        row = self.store.stats["rows"]["custom.collection.1.7"]
        self.assertEqual((row["n"], row["avg_ms"], row["max_ms"]), (2, 350, 400))

    def test_slow_threshold_is_a_setting(self):
        store = Store(tempfile.mkdtemp())                # fresh history: the shared one has already seen the log
        store.set_accounts({"1": "owner", "42": "alice"}, {"1": "100", "42": "42"})
        w = Warmer(store)
        w.S.update({"slow_load_seconds": 0.4})
        with mock.patch.object(Warmer, "refresh_server_info"):
            w.learn("tok", force=True)
        self.assertEqual(store.stats["hits_total"]["c"], {"new app or screen": 1, "own playback": 1})

    def test_devices_name_requests_by_address(self):
        self.store.note_players([("42", "192.168.1.20", {"title": "Living Room", "product": "Plex for Apple TV",
                                                          "platform": "tvOS", "model": "", "cid": "abc"})])
        self.assertEqual(self.store.device_for("42", "192.168.1.20"), "cid:abc")
        self.assertEqual(self.store.device_label("42", "cid:abc")[:2], ("Living Room", "Apple TV"))
        self.assertEqual(self.store.device_label("42", "ip:1.2.3.4")[0], "Device at 1.2.3.4")

    def test_entries_from_own_address_are_dropped(self):
        e = json.dumps(["/hubs/promoted?x=1", "en", ""])
        self.store.merge_learned({"42": {e: "10.9.9.9"}}, 14, 30)
        self.assertIn(e, self.store.learned["accounts"]["42"])
        self.store.merge_learned({}, 14, 30, drop_ips={"10.9.9.9"})
        self.assertNotIn("42", self.store.learned["accounts"])


class Plan(unittest.TestCase):
    def setUp(self):
        self.store = Store(tempfile.mkdtemp())
        self.store.set_accounts({"1": "owner", "42": "alice"}, {})
        e1, e2 = json.dumps(["/hubs/a", "en", ""]), json.dumps(["/hubs/b", "en", ""])
        self.store.merge_learned({"1": {e1: "1.1.1.1"}, "42": {e2: "2.2.2.2"}}, 14, 30)
        self.w = Warmer(self.store)

    def test_always_and_switches(self):
        todo, why, prio = self.w.plan()
        self.assertEqual(set(todo), {"1", "42"})
        self.w.S.set_enabled("accounts", "42", False)
        todo, why, prio = self.w.plan()
        self.assertEqual((set(todo), why["42"]), ({"1"}, "off"))
        self.w.S.set_enabled("accounts", "42", True)
        self.w.S.set_enabled("devices", "1|ip:1.1.1.1", False)
        todo, why, prio = self.w.plan()
        self.assertEqual((set(todo), why["1"]), ({"42"}, "all devices off"))

    def test_watching_accounts_are_skipped_then_warmed_first(self):
        self.w.playing, self.w.play_ips = {"42"}, {"42": {"2.2.2.2"}}     # alice plays on the device at 2.2.2.2
        todo, why, prio = self.w.plan()
        self.assertEqual(set(todo), {"1"})                                # her only requests are from that device
        self.assertEqual(why["42"], "watching now (warms when playback ends)")
        other = json.dumps(["/hubs/promoted?x=1", "en", ""])              # ... until her phone shows up
        self.store.merge_learned({"42": {other: "5.5.5.5"}}, 14, 30)
        todo, why, prio = self.w.plan()
        self.assertEqual((todo["42"], why["42"]), ([other], "watching now (other devices kept warm)"))
        self.store.learned["accounts"]["42"].pop(other)
        self.w.playing, self.w.after_play = set(), {"42": 9e12}
        todo, why, prio = self.w.plan()
        self.assertEqual((prio["42"], why["42"]), (0, "playback just ended"))
        e3 = json.dumps(["/hubs/c", "en", ""])
        self.store.merge_learned({"42": {e3: "9.9.9.9"}}, 14, 30)
        self.w.play_ips = {"42": {"2.2.2.2"}}           # was watching on the device at 2.2.2.2: its requests go first
        self.assertEqual(json.loads(self.w.plan()[0]["42"][0])[0], "/hubs/b")
        self.w.S.update({"skip_while_playing": 0})
        self.w.playing = {"42"}
        self.assertEqual(set(self.w.plan()[0]), {"1", "42"})

    def test_devices_behind_one_address_are_told_apart(self):
        tv, phone = json.dumps(["/hubs/promoted?tv=1", "en", ""]), json.dumps(["/hubs/promoted?phone=1", "en", ""])
        # both learned from the same public address, but Plex's traffic statistics named the device
        self.store.merge_learned({"42": {tv: "2.2.2.2", phone: "2.2.2.2"}}, 14, 30,
                                 devices={"42": {tv: "TV-CID", phone: "PHONE-CID"}})
        self.assertEqual(self.store.entry_device("42", tv), "cid:TV-CID")
        self.w.playing, self.w.play_ips, self.w.play_devs = {"42"}, {"42": {"2.2.2.2"}}, {"42": {"cid:TV-CID"}}
        todo, why, prio = self.w.plan()
        self.assertIn(phone, todo["42"])                 # the phone stays warm while the TV plays
        self.assertNotIn(tv, todo["42"])
        self.store.note_device_catalog({"PHONE-CID": {"name": "Alice's iPhone", "platform": "iOS"}})
        self.assertEqual(self.store.device_label("42", "cid:PHONE-CID"), ("Alice's iPhone", "iOS", True))

    def test_device_at_picks_the_browsing_device(self):
        w = self.w
        w.bw = {("42", "TV"): {100: 4000000, 101: 4000000, 102: 4000000},      # streaming
                ("42", "PHONE"): {101: 140000},                                  # a page load
                ("42", "TOOL"): {100: 5000, 101: 4000}}                           # a background poller
        w.playing, w.play_devs = {"42"}, {"42": {"cid:TV"}}
        self.assertEqual(w.device_at("42", 101), "PHONE")
        self.assertEqual(w.device_at("42", 500), None)                           # nobody was active
        del w.bw[("42", "PHONE")]
        self.assertEqual(w.device_at("42", 101), "TV")                           # only small pollers besides the TV
        w.bw[("42", "TABLET")] = {101: 90000}
        w.bw[("42", "PHONE")] = {101: 140000}
        self.assertEqual(w.device_at("42", 101), None)                           # two busy devices: no guess

    def test_library_being_watched_is_not_rebuilt(self):
        tv = json.dumps(["/hubs/promoted?contentDirectoryID=2&count=20", "en", ""])
        movies = json.dumps(["/hubs/promoted?contentDirectoryID=1&count=20", "en", ""])
        page = json.dumps(["/hubs/sections/2?count=12", "en", ""])
        home = json.dumps(["/hubs/promoted?excludeMusic=1", "en", ""])
        self.store.merge_learned({"42": {tv: "5.5.5.5", movies: "5.5.5.5", page: "5.5.5.5", home: "5.5.5.5"}}, 14, 30)
        self.w.playing, self.w.play_ips, self.w.play_secs = {"42"}, {"42": {"2.2.2.2"}}, {"42": {"2"}}   # watching TV
        todo, why, prio = self.w.plan()
        self.assertEqual(set(todo["42"]), {movies, home})        # TV-only requests would be dropped again at once
        for e in (tv, movies, page, home):
            self.store.learned["accounts"]["42"].pop(e)

    def test_collection_pass_covers_collection_screens_of_likely_accounts(self):
        home = json.dumps(["/hubs/promoted?excludeMusic=1", "en", ""])
        page = json.dumps(["/hubs/sections/2?count=12", "en", ""])
        cw = json.dumps(["/hubs/continueWatching?x=1", "en", ""])
        self.store.merge_learned({"42": {home: "5.5.5.5", page: "5.5.5.5", cw: "5.5.5.5"}}, 14, 30)
        self.w.S.update({"mode": "always"})
        todo, why, prio = self.w.plan(collections=True)            # always mode: everyone is likely
        self.assertEqual(set(todo["42"]), {home, page})
        self.assertNotIn("1", todo)                                # owner has no collection screens learned
        self.assertEqual(self.w.partial, {"42"})                   # not a full warm of the account
        self.w.S.update({"mode": "smart"})                         # nobody active, nobody's usual hours
        self.store.stats.pop("activity", None)
        todo, why, prio = self.w.plan(collections=True)
        self.assertEqual(todo, {})
        self.store.stats["activity"] = {"42": {"last": time.time(), "how": [0] * 168}}
        todo, why, prio = self.w.plan(collections=True)            # alice was active an hour ago
        self.assertEqual(set(todo["42"]), {home, page})
        self.w.S.update({"mode": None})
        for e in (home, page, cw):
            self.store.learned["accounts"]["42"].pop(e)

    def test_paused_stream_does_not_count_as_playing(self):
        def sessions(*streams):
            body = json.dumps({"MediaContainer": {"Metadata": [
                {"User": {"id": 42}, "Player": {"state": st, "address": ip, "machineIdentifier": cid, "title": cid}}
                for st, ip, cid in streams]}}).encode()
            return mock.patch.object(Warmer, "pms", return_value=(0.01, body))
        with sessions(("playing", "2.2.2.2", "TV"), ("paused", "2.2.2.2", "PHONE")):
            self.w.update_sessions("tok")
        self.assertEqual((self.w.playing, self.w.play_devs["42"]), ({"42"}, {"cid:TV"}))   # only the TV is playing
        with sessions(("paused", "2.2.2.2", "TV"), ("paused", "2.2.2.2", "PHONE")):
            self.w.update_sessions("tok")
        self.assertEqual(self.w.playing, set())
        self.assertIn("42", self.w.after_play)            # pausing warms the account at once, TV first
        self.assertEqual(self.w.play_devs["42"], {"cid:TV"})
        self.w.after_play.clear()

    def test_posters_are_prepared_in_each_accounts_form(self):
        from hubwarmer.core import photo_profile, hub_thumbs
        rel, thumb = photo_profile("/photo/:/transcode?width=321&height=481&minSize=1&upscale=1"
                                   "&url=%2Flibrary%2Fmetadata%2F7%2Fthumb%2F1700000000")
        tok, _ = photo_profile("/photo/:/transcode?height=719&width=480&url=http%3A%2F%2F127.0.0.1%3A32400"
                               "%2Flibrary%2Fmetadata%2F7%2Fthumb%2F1700000000%3FX-Plex-Token%3Dxxxx")
        self.assertEqual(thumb, "/library/metadata/7/thumb/1700000000")
        self.assertEqual((json.loads(rel)["token"], json.loads(tok)["token"], json.loads(tok)["host"]),
                         (False, True, "http://127.0.0.1:32400"))
        self.assertIsNone(photo_profile("/photo/:/transcode?width=1&url=https%3A%2F%2Fexample.org%2Fa.jpg"))
        body = json.dumps({"MediaContainer": {"Hub": [{"Metadata": [{"thumb": thumb}, {"thumb": "/other"}]}]}})
        self.assertEqual(hub_thumbs(body), [thumb])
        self.store.note_photo_profiles({"42": {rel: 40, tok: 25, "{\"odd\": 1}": 1, "{\"odd\": 2}": 2},
                                        "1": {tok: 9}}, 3)
        self.assertEqual(len(self.store.stats["photo_profiles"]["42"]), 3)   # most used forms kept
        self.assertNotIn("{\"odd\": 1}", self.store.stats["photo_profiles"]["42"])
        del self.store.stats["photo_profiles"]["42"]["{\"odd\": 2}"]
        self.w.queue_posters("42", [thumb])
        self.w.queue_posters("42", [thumb])                      # asked for a moment ago: not queued twice
        self.w.queue_posters("1", [thumb])                       # owner + token form: device tokens unknown
        self.assertEqual(len(self.w.photo_queue), 2)
        urls = []

        def fake_get(url, token, **kw):
            urls.append((url, token))
            return 0.2, b""
        with mock.patch("hubwarmer.core.get", fake_get):
            self.w.warm_posters({"42": ("alice", "t42")})
        self.assertEqual(len(urls), 2)
        self.assertTrue(any("X-Plex-Token%3Dt42" in u for u, _ in urls))          # per-user form uses her token
        self.assertTrue(any("url=%2Flibrary%2Fmetadata%2F7%2Fthumb" in u for u, _ in urls))
        self.assertEqual(self.store.stats["posters"]["built"], 2)

    def test_real_poster_requests_are_measured(self):
        store = Store(tempfile.mkdtemp())
        store.add_photo_loads([("2026-11-11T19:11:55", 4), ("2026-11-11T19:11:55", 50), ("2026-11-11T19:11:56", 210),
                               ("2026-11-11T20:00:01", 12)])
        h = store.stats["photo_hourly"]
        self.assertEqual(h["2026-11-11T19"], {"n": 3, "instant": 1, "slow": 1, "sum_ms": 264})
        self.assertEqual(h["2026-11-11T20"]["instant"], 1)

    def test_time_budget_and_order(self):
        calls = []

        def fake_get(url, tok, **kw):
            calls.append(tok)
            return 0.01, b""
        self.w.tokens, self.w.tokens_at = {"1": ("owner", "t1"), "42": ("alice", "t42")}, 9e12
        self.w.S.update({"pass_budget_seconds": 1})
        todo, why, prio = self.w.plan()
        prio["42"] = 0                                   # playback just ended: first, and never cut by the budget
        with mock.patch("hubwarmer.core.get", fake_get), mock.patch("hubwarmer.core.time.time", side_effect=range(0, 1000, 5)):
            self.w._warm(todo, prio, "t1")
        self.assertEqual(calls, ["t42"])                 # owner left for the next pass
        self.assertIn("42", self.store.learned["acct_last_warm"])
        self.assertNotIn("1", self.store.learned["acct_last_warm"])

    def test_smart_mode(self):
        self.w.S.update({"mode": "smart"})
        todo, why, prio = self.w.plan()                       # no usage data, never warmed -> idle refresh due
        self.assertTrue(all(r.startswith("out of hours") for r in why.values()))
        self.store.learned["acct_last_warm"] = {"1": 9e12, "42": 9e12}
        self.w.S.update({"home_always": 0})
        todo, why, prio = self.w.plan()
        self.assertEqual(todo, {})

    def test_home_first_keeps_every_accounts_home_warm(self):
        home, page = json.dumps(["/hubs/promoted?contentDirectoryID=1", "en", ""]), json.dumps(["/hubs/sections/1?count=12", "en", ""])
        self.store.merge_learned({"42": {home: "2.2.2.2", page: "2.2.2.2"}}, 14, 30)
        self.w.S.update({"mode": "smart"})
        self.store.learned["acct_last_warm"] = {"1": 9e12, "42": 9e12}     # idle refresh not due
        todo, why, prio = self.w.plan()
        self.assertEqual(todo, {"42": [home]})                             # Home only; account 1 has no Home request
        self.assertEqual((why["42"], prio["42"]), ("out of hours (home only)", 3))
        self.w.tokens, self.w.tokens_at = {"42": ("alice", "t42")}, 9e12
        with mock.patch("hubwarmer.core.get", lambda url, tok, **kw: (0.01, b"")):
            self.w._warm(todo, prio, "t1")
        self.assertEqual(self.store.learned["acct_last_warm"]["42"], 9e12)  # a Home-only pass does not postpone the full refresh
        self.assertEqual(self.store.stats["runs"][-1]["accounts"], 1)


if __name__ == "__main__":
    unittest.main()
