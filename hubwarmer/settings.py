"""Runtime settings: environment variables are the defaults, the owner can change them on the Settings page
(stored in config.json, applied on the next pass, no restart)."""
import json, os, urllib.parse, urllib.request

from . import config as C


def probe_plex(url):
    """Ask a Plex Media Server address who it is. -> (machine id, version). Raises ValueError with a plain reason."""
    u = urllib.parse.urlparse(url)
    if u.scheme not in ("http", "https") or not u.netloc:
        raise ValueError("the address must look like http://192.168.1.10:32400")
    try:
        req = urllib.request.Request(url.rstrip("/") + "/identity", headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            mc = json.loads(r.read())["MediaContainer"]
        return mc["machineIdentifier"], mc.get("version", "")
    except (KeyError, TypeError, ValueError):
        raise ValueError("something answered at that address, but it is not a Plex Media Server") from None
    except Exception as e:
        raise ValueError(f"no answer from that address ({getattr(e, 'reason', None) or type(e).__name__})") from None


# key: (type, env default, min, max, label, help)
DEFS = {
    "plex_url": (str, C.PLEX_URL, None, None, "Plex Media Server address",
                 "How this container reaches Plex directly, for example http://192.168.1.10:32400. Tested when saved."),
    "plex_logs": (str, C.PLEX_LOGS, None, None, "Log source",
                  "api: download the logs from Plex. Or the path of a mounted Plex Logs folder, which is read on every "
                  "check."),
    "mode": (str, C.env("WARM_MODE", "smart"), None, None, "Mode",
             "smart: warm each account around the hours it normally uses Plex and after recent activity; other "
             "accounts only on the idle refresh. always: warm every account on every pass."),
    "interval_seconds": (int, C.INTERVAL, 30, 3600, "Check interval (seconds)",
                         "How often the probe request runs. A slow probe means the cache was cleared and starts a pass."),
    "top_up_minutes": (float, C.TOP_UP_MIN, 1, 1440, "Scheduled pass interval (minutes)",
                       "A pass runs at least this often, whatever the probe reports."),
    "learn_minutes": (float, C.LEARN_MIN, 2, 1440, "Log read interval (minutes)",
                      "How often the Plex log is read for new clients and load times."),
    "keep_days": (float, C.KEEP_DAYS, 0.5, 365, "Request retention (days)",
                  "A learned request is dropped when its client has not used it for this long."),
    "max_requests_per_account": (int, C.MAX_PER_ACCOUNT, 1, 500, "Requests per account",
                                 "Upper limit. The most recently used requests are kept."),
    "pause_above_transcodes": (int, C.PAUSE_ABOVE, 0, 100, "Pause above (transcodes)",
                               "Skip passes while more than this many transcodes are running. 0 disables the limit."),
    "smart_idle_minutes": (float, float(C.env("SMART_IDLE_MINUTES", "60")), 5, 1440, "Idle refresh interval (minutes)",
                           "Smart mode: how often accounts outside their usual hours are warmed."),
    "smart_recent_hours": (float, float(C.env("SMART_RECENT_HOURS", "2")), 0, 48, "Recent activity window (hours)",
                           "Smart mode: accounts active within this window are warmed on every pass."),
    "home_always": (int, int(C.env("HOME_ALWAYS", "1")), 0, 1, "Keep Home warm for every account",
                    "Smart mode: when on, the Home screen requests of every account are replayed on every pass, so "
                    "Home is ready whenever someone opens Plex. Library pages follow the smart schedule."),
    "skip_while_playing": (int, int(C.env("SKIP_WHILE_PLAYING", "1")), 0, 1, "Skip the playing device",
                           "When on, the device that is playing is skipped until playback ends; the account's other "
                           "devices are still kept warm. When off, playback is ignored."),
    "session_poll_seconds": (int, int(C.env("SESSION_POLL_SECONDS", "15")), 5, 600, "Session check interval (seconds)",
                             "How often active playback sessions are checked."),
    "pass_budget_seconds": (int, int(C.env("PASS_BUDGET_SECONDS", "90")), 0, 3600, "Pass time budget (seconds)",
                            "No new accounts are started after this long. Remaining accounts go first on the next "
                            "pass. 0 disables the limit."),
    "warm_on_library_change": (int, int(C.env("WARM_ON_LIBRARY_CHANGE", "1")), 0, 1, "Warm when the library changes",
                               "When on, Plex's notification feed is watched and a pass starts as soon as items "
                               "have been added, changed or removed, instead of at the next scheduled check."),
    "library_settle_seconds": (int, int(C.env("LIBRARY_SETTLE_SECONDS", "30")), 5, 600, "Library change settle time (seconds)",
                               "After a library change, wait until no further change has arrived for this long (a "
                               "scan adds many items in a row), at most five times as long. Also the minimum gap "
                               "after the previous pass."),
    "warm_posters": (int, int(C.env("WARM_POSTERS", "1")), 0, 1, "Prepare posters",
                     "Plex resizes each poster for the device that asks and keeps the result. When on, the posters "
                     "of every warmed screen are requested in advance, in the sizes each account's apps use."),
    "poster_budget_seconds": (int, int(C.env("POSTER_BUDGET_SECONDS", "45")), 5, 600, "Poster time budget (seconds)",
                              "Time spent on posters after each pass. The rest stay queued for the next pass."),
    "slow_load_seconds": (float, float(C.env("SLOW_LOAD_SECONDS", "1.0")), 0.25, 30, "Slow load threshold (seconds)",
                          "Row loads longer than this are reported as slow loads, with their cause."),
    "probe_slow_seconds": (float, C.SLOW_S, 0.1, 30, "Probe threshold (seconds)",
                           "A probe slower than this is treated as a cleared cache."),
    "history_days": (float, C.HISTORY_DAYS, 1, 90, "History (days)",
                     "How long hourly statistics and the list of passes are kept."),
    "self_ips": (str, ",".join(sorted(C.SELF_IPS)), None, None, "Own addresses",
                 "Addresses Plex sees this container's requests from, comma separated. Requests from them are never "
                 "learned. Normally found automatically; 127.0.0.1 always counts."),
    "public_url": (str, C.PUBLIC_URL, None, None, "Public address of this page",
                   "Where the browser returns after Sign in with Plex, for example https://warmer.example.com. Only "
                   "needed when a reverse proxy does not pass the original host name on."),
    "session_days": (float, C.SESSION_DAYS, 1, 365, "Sign-in lifetime (days)",
                     "How long a sign-in on this page lasts. Applies to new sign-ins."),
    "metrics_public": (int, 1 if C.METRICS_PUBLIC else 0, 0, 1, "Metrics without sign-in",
                       "When on, /metrics can be read without signing in, as Prometheus needs. The metrics hold "
                       "counts and timings, no names."),
}


class Settings:
    def __init__(self, store):
        self.store = store

    def get(self, key):
        typ, default = DEFS[key][0], DEFS[key][1]
        v = self.store.config.get("settings", {}).get(key)
        return typ(default) if v is None else typ(v)

    def describe(self):
        cur = self.store.config.get("settings", {})
        return [{"key": k, "label": d[4], "help": d[5], "value": self.get(k), "default": d[0](d[1]),
                 "changed": k in cur, "min": d[2], "max": d[3],
                 "type": "switch" if (d[0] is int and d[2] == 0 and d[3] == 1) else d[0].__name__,
                 "example": {"plex_url": "http://192.168.1.10:32400", "plex_logs": "api",
                             "public_url": "https://warmer.example.com", "self_ips": "found automatically"}.get(k, "")}
                for k, d in DEFS.items()]

    def update(self, changes):
        """changes: {key: value or None (= back to default)} -> list of errors"""
        errors, new = [], dict(self.store.config.get("settings", {}))
        for k, v in changes.items():
            if k not in DEFS:
                errors.append(f"unknown setting {k}")
                continue
            typ, _, lo, hi = DEFS[k][:4]
            if v is None or v == "":
                new.pop(k, None)
                continue
            try:
                v = typ(v)
            except (TypeError, ValueError):
                errors.append(f"{DEFS[k][4]}: not a valid {typ.__name__}")
                continue
            if typ is str:
                v = v.strip()
            if k == "mode" and v not in ("always", "smart"):
                errors.append("mode must be 'always' or 'smart'")
                continue
            if k == "plex_url":
                v = v.rstrip("/")
                try:
                    probe_plex(v)
                except ValueError as e:
                    errors.append(f"{DEFS[k][4]}: {e}")
                    continue
            if k == "plex_logs" and v != "api" and not os.path.isdir(v):
                errors.append(f"{DEFS[k][4]}: must be 'api' or a folder that exists in the container")
                continue
            if k == "public_url":
                v = v.rstrip("/")
                if urllib.parse.urlparse(v).scheme not in ("http", "https"):
                    errors.append(f"{DEFS[k][4]}: must start with http:// or https://")
                    continue
            if lo is not None and not (lo <= v <= hi):
                errors.append(f"{DEFS[k][4]}: must be between {lo} and {hi}")
                continue
            new[k] = v
        if not errors:
            with self.store.lock:
                self.store.config["settings"] = new
                self.store.save_config()
        return errors

    def self_ips(self):
        return {"127.0.0.1"} | set(filter(None, (x.strip() for x in self.get("self_ips").split(","))))

    # ---------- per account / device on-off ----------
    def disabled(self, kind):
        return set(self.store.config.get("disabled", {}).get(kind, []))

    def set_enabled(self, kind, ident, enabled):
        with self.store.lock:
            d = self.store.config.setdefault("disabled", {}).setdefault(kind, [])
            if enabled and ident in d:
                d.remove(ident)
            elif not enabled and ident not in d:
                d.append(ident)
            self.store.save_config()
