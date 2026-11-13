"""python -m hubwarmer [--once] [--user NAME|ID] [--force] [--dry-run]

Default: warm loop + web page (/), JSON (/api/status) and Prometheus metrics (/metrics) on WEB_PORT."""
import argparse, signal, sys, threading

from . import __version__
from .core import Warmer
from .store import Store, log
from . import web


def main():
    ap = argparse.ArgumentParser(prog="hub-warmer", description=__doc__.splitlines()[0])
    ap.add_argument("--once", action="store_true", help="one pass, no web page, then exit")
    ap.add_argument("--user", help="only warm this account (name or id) - implies --once")
    ap.add_argument("--force", action="store_true", help="warm even if the probe is fast")
    ap.add_argument("--dry-run", action="store_true", help="show what would be warmed - implies --once")
    ap.add_argument("--version", action="version", version=__version__)
    a = ap.parse_args()

    store = Store()
    warmer = Warmer(store)
    if a.once or a.user or a.dry_run:
        if not warmer.S.get("plex_url"):
            sys.exit("no Plex address yet: open the web page once, or set PLEX_URL")
        warmer.learn(warmer.owner_token() or sys.exit("no owner token (PLEX_TOKEN, Preferences.xml or web sign-in)"),
                     force=True)
        warmer.run_once(force=a.force or bool(a.user), only_user=a.user, dry_run=a.dry_run)
        return 0

    stop = threading.Event()

    def on_signal(*_):
        log("stopping")
        stop.set()
        warmer.wake.set()
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    web.serve(store, warmer, stop)
    warmer.loop(stop)
    return 0


if __name__ == "__main__":
    sys.exit(main())
