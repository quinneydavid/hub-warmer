"""Prometheus text exposition (no client library). No usernames in labels."""
import time

from . import __version__
from .store import LOAD_BUCKETS_MS


def render(store, warmer):
    with store.lock:
        s = store.stats
        tot = s.get("totals", {})
        lt = s.get("loads_total", {})
        accts = store.learned.get("accounts", {})
        probe = s.get("last_probe", {})
        out = []

        def m(name, kind, help_, value, labels=""):
            out.append(f"# HELP hub_warmer_{name} {help_}")
            out.append(f"# TYPE hub_warmer_{name} {kind}")
            out.append(f"hub_warmer_{name}{labels} {value}")

        m("info", "gauge", "Build info.", 1, f'{{version="{__version__}"}}')
        m("up", "gauge", "1 if the last pass succeeded.", 0 if warmer.state in ("error", "waiting-for-setup") else 1)
        m("setup_needed", "gauge", "1 until the server owner signs in (no owner token).",
          1 if warmer.state == "waiting-for-setup" else 0)
        m("debug_logging_ok", "gauge", "1 if Plex debug logging (HubCache lines) is on.",
          1 if s.get("debug_logging_ok") else 0)
        m("runs_total", "counter", "Warm passes.", tot.get("runs", 0))
        m("requests_total", "counter", "Hub requests replayed.", tot.get("requests", 0))
        m("cold_requests_total", "counter", "Replayed requests that were not cached (Plex rebuilt them).",
          tot.get("cold", 0))
        m("cold_seconds_total", "counter",
          "Seconds Plex spent rebuilding rows during warming - waiting users would otherwise have absorbed.",
          round(tot.get("cold_seconds", 0.0), 3))
        m("busy_seconds_total", "counter", "Seconds spent warming.", round(tot.get("busy_seconds", 0.0), 3))
        m("errors_total", "counter", "Failed replays.", tot.get("errors", 0))
        m("last_warm_timestamp_seconds", "gauge", "Unix time of the last full warm.",
          round(store.learned.get("last_warm", 0)))
        m("probe_seconds", "gauge", "Latest probe request duration.", probe.get("seconds", 0))
        m("accounts", "gauge", "Accounts with learned requests.", len(accts))
        m("learned_requests", "gauge", "Distinct hub requests being kept warm.", sum(len(v) for v in accts.values()))
        if "plex_hit_ratio" in s:
            m("plex_hubcache_hit_ratio", "gauge",
              "Plex's own hub cache hit ratio (0-1), from its log. Includes the warmer's requests - "
              "see user_row_cache_* for what real users got.",
              round(s["plex_hit_ratio"] / 100, 4))
            m("plex_hubcache_entries", "gauge", "Hubs in Plex's cache, from its log.", s.get("plex_hub_entries", 0))

        m("accounts_watching", "gauge", "Accounts with a live playback session (not warmed meanwhile).",
          len(warmer.playing))
        ht = s.get("hits_total", {})
        m("user_row_cache_requests_total", "counter", "Real users' cacheable row requests.", ht.get("n", 0))
        m("user_row_cache_hits_total", "counter", "Of those, served entirely from Plex's cache.", ht.get("hit", 0))
        out.append("# HELP hub_warmer_user_slow_loads_total Real users' row loads over the slow-load threshold, by cause.")
        out.append("# TYPE hub_warmer_user_slow_loads_total counter")
        for cause in ("own playback", "library change", "row changed", "new app or screen", "other", "not cache related"):
            out.append(f'hub_warmer_user_slow_loads_total{{cause="{cause}"}} {ht.get("c", {}).get(cause, 0)}')

        po = s.get("posters", {})
        m("poster_requests_total", "counter", "Posters requested in advance.", po.get("requests", 0))
        m("posters_built_total", "counter", "Of those, not yet in Plex's image cache (Plex resized them).",
          po.get("built", 0))
        m("posters_queued", "gauge", "Posters waiting to be requested.", po.get("queued", 0))
        pht = s.get("photo_hourly", {})
        m("user_poster_requests_total", "counter", "Real apps' poster requests (history window).",
          sum(v["n"] for v in pht.values()))
        m("user_poster_instant_total", "counter", "Of those, answered from Plex's image cache (30 ms or less).",
          sum(v["instant"] for v in pht.values()))

        name = "hub_warmer_user_row_load_seconds"
        out.append(f"# HELP {name} Real users' Home/library row loads (from the Plex request log).")
        out.append(f"# TYPE {name} histogram")
        for b in LOAD_BUCKETS_MS:
            out.append(f'{name}_bucket{{le="{b / 1000:g}"}} {lt.get("le", {}).get(str(b), 0)}')
        out.append(f'{name}_bucket{{le="+Inf"}} {lt.get("count", 0)}')
        out.append(f"{name}_sum {round(lt.get('sum_ms', 0) / 1000, 3)}")
        out.append(f"{name}_count {lt.get('count', 0)}")
        out.append(f"# scraped {int(time.time())}")
    return "\n".join(out) + "\n"
