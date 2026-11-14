"""Library changes from Plex's notification feed, and the miss causes added with it."""
import json, os, sys, tempfile, threading, time, unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())


# Imported when the tests run, not when the module loads: test discovery loads every module first, and other test
# modules set their environment before their first import of hubwarmer (its defaults are read at import time).
def miss_cause(*a, **kw):
    from hubwarmer.core import miss_cause as f
    return f(*a, **kw)


def library_changes(*a):
    from hubwarmer.events import library_changes as f
    return f(*a)


def read_events(*a):
    from hubwarmer.events import read_events as f
    return f(*a)


def LibraryEvents(w):
    from hubwarmer.events import LibraryEvents as c
    return c(w)


def Store():
    from hubwarmer.store import Store as c
    return c()


def entry(**kw):
    return dict({"identifier": "com.plexapp.plugins.library", "sectionID": "2", "itemID": "123", "type": 4,
                 "title": "Episode 3", "state": 5}, **kw)


class Changes(unittest.TestCase):
    def test_processed_item(self):
        self.assertEqual(library_changes("timeline", {"TimelineEntry": [entry()]}), ["episode updated: Episode 3"])

    def test_websocket_form(self):
        data = {"NotificationContainer": {"type": "timeline", "size": 1, "TimelineEntry": [entry(state=9, type=1)]}}
        self.assertEqual(library_changes("", data), ["movie removed: Episode 3"])

    def test_states_in_progress_are_ignored(self):
        for state in (0, 1, 2, 3, 4, 6):
            self.assertEqual(library_changes("timeline", {"TimelineEntry": [entry(state=state)]}), [])

    def test_other_identifiers_and_no_section_are_ignored(self):
        self.assertEqual(library_changes("timeline", {"TimelineEntry": [entry(identifier="com.plexapp.system")]}), [])
        self.assertEqual(library_changes("timeline", {"TimelineEntry": [entry(sectionID="-1")]}), [])

    def test_single_objects_instead_of_lists(self):
        self.assertEqual(library_changes("timeline", {"TimelineEntry": entry()}), ["episode updated: Episode 3"])
        data = {"ActivityNotification": {"event": "ended", "Activity": {"type": "library.refresh.items"}}}
        self.assertEqual(library_changes("activity", data), ["library.refresh.items finished"])

    def test_collection_edit(self):
        self.assertEqual(library_changes("timeline", {"TimelineEntry": [entry(type=18, title="Trending")]}),
                         ["collection updated: Trending"])

    def test_scan_finished(self):
        data = {"event": "ended", "uuid": "x", "Activity": {"type": "library.update.section", "title": "Scanning TV"}}
        self.assertEqual(library_changes("activity", data), ["Scanning TV finished"])
        data["event"] = "progress"
        self.assertEqual(library_changes("activity", data), [])

    def test_other_activities_are_ignored(self):
        data = {"event": "ended", "Activity": {"type": "media.generate.bif", "title": "Thumbnails"}}
        self.assertEqual(library_changes("activity", data), [])

    def test_garbage(self):
        for d in (None, [], "x", {"TimelineEntry": "x"}, {"TimelineEntry": [1, None]}, {"Activity": "x"}):
            self.assertEqual(library_changes("timeline", d), [])


class EventStream(unittest.TestCase):
    def test_parses_events(self):
        lines = [b"event: ping\n", b"data: {}\n", b"\n",
                 b"event: timeline\n", b'data: {"TimelineEntry": [{"state": 5}]}\n', b"\n",
                 b"data: not json\n", b"\n"]
        self.assertEqual(list(read_events(lines)), [("ping", {}), ("timeline", {"TimelineEntry": [{"state": 5}]})])


class FakeSettings:
    def __init__(self, **kw):
        self.v = dict({"warm_on_library_change": 1, "library_settle_seconds": 30, "plex_url": ""}, **kw)

    def get(self, k):
        return self.v[k]


class FakeWarmer:
    def __init__(self, **kw):
        self.S = FakeSettings(**kw)
        self.store = type("St", (), {"client_id": "test-client"})()

    def owner_token(self):
        return "tok"


class Due(unittest.TestCase):
    def test_settles_then_fires_once(self):
        ev = LibraryEvents(FakeWarmer())
        self.assertFalse(ev.due(0))
        ev.note(["episode updated: a", "episode updated: b"])
        self.assertFalse(ev.due(0))                      # changes still arriving
        ev.last -= 31
        self.assertTrue(ev.due(0))
        label = ev.take()
        self.assertIn("library change: episode updated: a; episode updated: b", label)
        self.assertFalse(ev.due(0))
        self.assertEqual(ev.passes_total, 1)

    def test_long_scan_fires_after_five_settle_periods(self):
        ev = LibraryEvents(FakeWarmer())
        ev.note(["x"] * 5)
        ev.first -= 151                                  # still busy, but the first change is 151 s old
        self.assertTrue(ev.due(0))
        self.assertIn("+2 more", ev.take())

    def test_gap_after_a_pass(self):
        ev = LibraryEvents(FakeWarmer())
        ev.note(["x"])
        ev.last -= 60
        self.assertFalse(ev.due(time.time() - 10))       # a pass ended 10 s ago
        self.assertTrue(ev.due(time.time() - 40))

    def test_empty_changes_are_ignored(self):
        ev = LibraryEvents(FakeWarmer())
        ev.note([])
        self.assertEqual(ev.pending, 0)


class Feed(unittest.TestCase):
    """Run the listener against a small fake Plex event feed."""

    def test_listener_notes_changes(self):
        seen = {}

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                seen["path"], seen["token"] = self.path, self.headers.get("X-Plex-Token")
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                body = ("event: timeline\ndata: " + json.dumps({"TimelineEntry": [entry()]}) + "\n\n"
                        "event: timeline\ndata: " + json.dumps({"TimelineEntry": [entry(state=2)]}) + "\n\n")
                self.wfile.write(body.encode())
                self.wfile.flush()
                time.sleep(0.5)

        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        ev = LibraryEvents(FakeWarmer(plex_url=f"http://127.0.0.1:{srv.server_address[1]}"))
        stop = threading.Event()
        ev.start(stop)
        for _ in range(50):
            if ev.pending:
                break
            time.sleep(0.05)
        stop.set()
        srv.shutdown()
        self.assertEqual(ev.pending, 1)
        self.assertTrue(seen["path"].startswith("/:/eventsource/notifications?filters=timeline,activity"))
        self.assertEqual(seen["token"], "tok")


class MissCauses(unittest.TestCase):
    def test_causes(self):
        self.assertEqual(miss_cause(None), "other")
        self.assertEqual(miss_cause("playbackStarted"), "own playback")
        self.assertEqual(miss_cause(""), "row changed")
        self.assertEqual(miss_cause("library.changed"), "library change")
        self.assertEqual(miss_cause("library.changed", age=1800), "not rewarmed")
        self.assertEqual(miss_cause("library.changed", age=600), "library change")

    def test_all_rebuilt_loads_are_counted_by_cause(self):
        st = Store()
        st.add_hits([("2026-11-14T10:00:00", "1", "hit", 20, 0, "/hubs/promoted", "Home", []),
                     ("2026-11-14T10:01:00", "1", "own playback", 400, 2, "/hubs/promoted", "Home", []),
                     ("2026-11-14T10:02:00", "1", "own playback", 1500, 2, "/hubs/promoted", "Home", [])], 1000)
        tot = st.stats["hits_total"]
        self.assertEqual(tot["m"], {"own playback": 2})       # slow or not
        self.assertEqual(tot["c"], {"own playback": 1})       # slow only, as before


if __name__ == "__main__":
    unittest.main()
