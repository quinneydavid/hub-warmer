#!/usr/bin/env python3
"""Run Hub Warmer with invented demo data: python3 tools/demo/run.py [--port 8099] [--data-dir DIR]

Starts the fake Plex server from fake_plex.py and Hub Warmer itself in one process. Hub Warmer runs unchanged,
except that its requests to the demo Plex address and to plex.tv go to the fake server (any other address is
refused) and Sign in with Plex is approved at once. Nothing is sent outside this machine.

On the page: enter http://plex.example:32400, then choose Sign in with Plex."""
import argparse, json, os, sys, tempfile, time, urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.dirname(os.path.dirname(HERE))]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--data-dir", help="default: a new temporary folder (the demo then starts at the setup page)")
    a = ap.parse_args()
    data_dir = a.data_dir or tempfile.mkdtemp(prefix="hub-warmer-demo-")

    # start-up options are read when hubwarmer is imported: set them first, and ignore whatever the shell has
    for name in list(os.environ):
        if name.startswith(("PLEX_", "WEB_", "WARM_")) or name in ("PUBLIC_URL", "SELF_IPS", "INCLUDE_USERS", "EXCLUDE_USERS"):
            del os.environ[name]
    os.environ.update(DATA_DIR=data_dir, WEB_PORT=str(a.port), WEB_BIND="127.0.0.1", WEB_AUTH="plex",
                      PLEX_PREFERENCES=os.path.join(data_dir, "no-preferences.xml"))

    import demo_data
    from fake_plex import FakePlex

    now = time.time()
    stats = os.path.join(data_dir, "stats.json")
    if not os.path.exists(stats):
        os.makedirs(data_dir, exist_ok=True)
        with open(stats, "w") as f:
            json.dump(demo_data.seed_stats(now), f)
    fake = "http://127.0.0.1:%d" % FakePlex(now).serve().server_address[1]

    real_urlopen = urllib.request.urlopen

    def urlopen(req, *args, **kw):
        url = req if isinstance(req, str) else req.full_url
        for prefix in (demo_data.PLEX_URL, "https://plex.tv"):
            if url.startswith(prefix + "/"):
                if isinstance(req, str):
                    req = fake + url[len(prefix):]
                else:
                    req.full_url = fake + url[len(prefix):]
                return real_urlopen(req, *args, **kw)
        raise urllib.error.URLError("the demo only knows " + demo_data.PLEX_URL)
    urllib.request.urlopen = urlopen

    from hubwarmer import auth
    from hubwarmer.__main__ import main as hub_warmer

    auth.auth_url = lambda client_id, code, forward_url: forward_url     # no plex.tv page: straight back, approved
    print(f"demo: http://127.0.0.1:{a.port} - enter {demo_data.PLEX_URL}, then Sign in with Plex (data in {data_dir})",
          flush=True)
    sys.argv = ["hub-warmer"]
    return hub_warmer()


if __name__ == "__main__":
    sys.exit(main())
