"""Parsing of Plex log lines and HubCache keys - the part most likely to break when Plex changes."""
import os, sys, tempfile, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())

from hubwarmer.core import DONE, HITRATIO, HUBKEY, REQ, _iso, describe, is_row_request, parse_key  # noqa: E402


class HubKey(unittest.TestCase):
    def test_web_app_library_page(self):
        k = "1/custom.collection.1.1003665/1/hubs/sections/1/enexternal-media,indirect-media,hub-style-list/count=12&excludeFields=summary"
        self.assertEqual(parse_key(k), ("1", "/hubs/sections/1?count=12&excludeFields=summary", "en",
                                        "external-media,indirect-media,hub-style-list"))

    def test_tv_app_home_with_region_language(self):
        k = "2273019/home.ondeck/1/hubs/promoted/en-GBexternal-media,indirect-media/contentDirectoryID=1&count=10"
        self.assertEqual(parse_key(k), ("2273019", "/hubs/promoted?contentDirectoryID=1&count=10", "en-GB",
                                        "external-media,indirect-media"))

    def test_no_language_no_features(self):
        k = "1/home.continue/1/hubs/promoted//contentDirectoryID=1&count=20&excludeContinueWatching=1"
        self.assertEqual(parse_key(k)[2:], ("", ""))

    def test_language_only(self):
        self.assertEqual(parse_key("637860174/home.continue/1/hubs/continueWatching/en-GB/skipRefresh=1")[1:],
                         ("/hubs/continueWatching?skipRefresh=1", "en-GB", ""))

    def test_ignored_paths(self):
        self.assertIsNone(parse_key("1/x/1/hubs/sections/1/manage/en/foo=1"))
        self.assertIsNone(parse_key("1/x/1/library/sections/1/all/en/foo=1"))
        self.assertFalse(is_row_request("/hubs/sections/2/continueWatching/items"))
        self.assertTrue(is_row_request("/hubs/promoted?count=1"))

    def test_hubcache_line(self):
        line = ("Nov 09, 2026 15:17:49.093 [1282] DEBUG - [Req#a465c7] HubCache: Computed "
                "'1/custom.collection.2.1003646/1/hubs/promoted/en/contentDirectoryID=2&count=20' in 82 ms.")
        m = HUBKEY.search(line)
        self.assertEqual(m.group(1), "a465c7")
        self.assertTrue(m.group(3).startswith("1/custom.collection.2.1003646/"))

    def test_hit_ratio(self):
        m = HITRATIO.search("DEBUG - [Req#a] HubCache: 813 hubs cached, 59.9% hit ratio.")
        self.assertEqual(m.groups(), ("813", "59.9"))


class RequestLines(unittest.TestCase):
    def test_request_ipv4_with_user(self):
        line = ("Nov 09, 2026 13:21:09.1 [1] DEBUG - Request: [192.168.1.62:34188 (WAN)] GET /hubs/promoted?a=1 "
                "(26 live) #a42a31 GZIP Signed-in Token (alice)")
        self.assertEqual(REQ.search(line).groups(), ("192.168.1.62", "/hubs/promoted?a=1", "a42a31", "alice"))

    def test_request_ipv6_display_name(self):
        line = ("Nov 09, 2026 16:04:19.009 [1] DEBUG - Request: [[::1]:53386 (Loopback)] GET /identity?hubwarmer_self=x "
                "(36 live) #a5a8ae Signed-in Token (Bob Example)")
        self.assertEqual(REQ.search(line).groups(), ("::1", "/identity?hubwarmer_self=x", "a5a8ae", "Bob Example"))

    def test_completed_variants(self):
        a = "Completed: [203.0.113.9:62585] 200 GET /hubs/promoted?count=20 (31 live) #9c9999 TLS GZIP 4856ms 137865 bytes"
        b = "Completed: [127.0.0.1:35742] 200 GET /hubs/promoted?x=1 (37 live) #a465c7 1591ms 458477 bytes (pipelined: 1)"
        c = "Completed: [[::1]:53386] 200 GET /identity?hubwarmer_self=x (36 live) #a5a8ae 0ms 418 bytes"
        self.assertEqual(DONE.search(a).group(5), "4856")
        self.assertEqual(DONE.search(b).groups()[3:], ("a465c7", "1591"))
        self.assertEqual(DONE.search(c).groups()[0], "::1")

    def test_timestamp(self):
        self.assertEqual(_iso("Nov 09, 2026 15:17:49"), "2026-11-09T15:17:49")
        self.assertEqual(_iso("garbage"), "")


class Describe(unittest.TestCase):
    def test_labels(self):
        s = {"1": "Movies", "2": "TV Shows"}
        self.assertEqual(describe("/hubs/sections/2?count=12", "en", "x,hub-style-list", s), ("TV Shows page", "web", "en"))
        self.assertEqual(describe("/hubs/promoted?contentDirectoryID=1&count=20", "en-GB", "external-media", s),
                         ("Home · Movies", "TV/mobile app", "en-GB"))
        self.assertEqual(describe("/hubs/continueWatching?x=1", "", "", s), ("Continue Watching", "app", "-"))


if __name__ == "__main__":
    unittest.main()
