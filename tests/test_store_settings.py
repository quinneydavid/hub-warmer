import os, sys, tempfile, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())

from hubwarmer import auth  # noqa: E402
from hubwarmer.settings import Settings  # noqa: E402
from hubwarmer.store import Store, _add_load, _new_load, load_summary, merge_loads  # noqa: E402


class Loads(unittest.TestCase):
    def test_summary(self):
        r = _new_load()
        for ms in (100, 200, 900, 1500, 4000, 12000):
            _add_load(r, ms)
        s = load_summary(r)
        self.assertEqual((s["n"], s["under1s"], s["p50_ms"]), (6, 50.0, 1000))
        self.assertEqual(load_summary(_new_load())["under1s"], None)
        self.assertEqual(merge_loads([r, r])["n"], 12)


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.S = Settings(Store(tempfile.mkdtemp()))

    def test_update_validate_reset(self):
        self.assertEqual(self.S.update({"interval_seconds": "90", "mode": "smart"}), [])
        self.assertEqual((self.S.get("interval_seconds"), self.S.get("mode")), (90, "smart"))
        self.assertTrue(self.S.update({"interval_seconds": 5}))          # out of range -> error, nothing saved
        self.assertTrue(self.S.update({"mode": "sometimes"}))
        self.assertTrue(self.S.update({"nope": 1}))
        self.assertEqual(self.S.update({"interval_seconds": None}), [])  # back to default
        self.assertNotEqual(self.S.get("interval_seconds"), 90)

    def test_config_file_is_private(self):
        st = self.S.store
        self.assertEqual(os.stat(st.p_config).st_mode & 0o777, 0o600)


class Sessions(unittest.TestCase):
    def test_roundtrip_and_tamper(self):
        user = {"id": "42", "username": "alice", "title": "Alice", "thumb": ""}
        c = auth.make_session("s3cret", user, "friend")
        self.assertEqual(auth.read_session("s3cret", f"x=1; {auth.COOKIE}={c}")["role"], "friend")
        self.assertIsNone(auth.read_session("other", f"{auth.COOKIE}={c}"))
        payload, sig = c.rsplit(".", 1)
        self.assertIsNone(auth.read_session("s3cret", f"{auth.COOKIE}={payload}x.{sig}"))
        self.assertIsNone(auth.read_session("s3cret", None))


if __name__ == "__main__":
    unittest.main()
