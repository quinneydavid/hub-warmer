"""Persistent data in DATA_DIR: learned.json (what to warm), stats.json (history, devices, usage), config.json
(owner token, session secret, settings, on/off switches - mode 600). One lock: the web thread reads while the
warmer writes."""
import datetime, json, os, secrets, threading, time

from . import config as C

LOAD_BUCKETS_MS = (250, 500, 1000, 2000, 5000, 10000)    # real users' row load time buckets
POSTER_INSTANT_MS, POSTER_SLOW_MS = 30, 80               # a poster request: already resized / had to be resized


def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


def _load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save(path, data, private=False):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600 if private else 0o644)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    os.replace(tmp, path)


def _new_load():
    return {"n": 0, "sum_ms": 0, "b": [0] * (len(LOAD_BUCKETS_MS) + 1)}


def _add_load(rec, ms):
    rec["n"] += 1
    rec["sum_ms"] += ms
    for i, b in enumerate(LOAD_BUCKETS_MS):
        if ms <= b:
            rec["b"][i] += 1
            return
    rec["b"][-1] += 1


def load_summary(rec):
    """-> {n, under1s %, avg_ms, p50_ms, p95_ms} (percentiles = bucket upper bounds)"""
    n = rec.get("n", 0)
    if not n:
        return {"n": 0, "under1s": None, "avg_ms": None, "p50_ms": None, "p95_ms": None,
                "b": [0] * (len(LOAD_BUCKETS_MS) + 1)}
    b, cum, p50, p95 = rec["b"], 0, None, None
    bounds = list(LOAD_BUCKETS_MS) + [20000]
    for i, c in enumerate(b):
        cum += c
        if p50 is None and cum >= 0.5 * n:
            p50 = bounds[i]
        if p95 is None and cum >= 0.95 * n:
            p95 = bounds[i]
    under1 = sum(b[:LOAD_BUCKETS_MS.index(1000) + 1])
    return {"n": n, "under1s": round(100 * under1 / n, 1), "avg_ms": round(rec["sum_ms"] / n), "p50_ms": p50, "p95_ms": p95,
            "b": list(b)}


def merge_loads(recs):
    out = _new_load()
    for r in recs:
        out["n"] += r.get("n", 0)
        out["sum_ms"] += r.get("sum_ms", 0)
        out["b"] = [a + c for a, c in zip(out["b"], r.get("b", [0] * len(out["b"])))]
    return out


class Store:
    def __init__(self, data_dir=C.DATA_DIR):
        self.lock = threading.RLock()
        self.p_learned = os.path.join(data_dir, "learned.json")
        self.p_stats = os.path.join(data_dir, "stats.json")
        self.p_config = os.path.join(data_dir, "config.json")
        self.learned = _load(self.p_learned, {})
        if self.learned.get("format") != 3:        # pre-0.3: no source addresses -> relearn once from the logs
            self.learned = {"format": 3}
        self.stats = _load(self.p_stats, {})
        if self.stats.get("format") != 3:          # older history formats: start the history fresh
            keep = {k: self.stats[k] for k in ("totals", "first_run") if k in self.stats}
            self.stats = dict(keep, format=3)
        self.config = _load(self.p_config, {})
        changed = False
        for k, gen in (("secret", lambda: secrets.token_hex(32)), ("client_id", lambda: "hub-warmer-" + secrets.token_hex(8))):
            if not self.config.get(k):
                self.config[k] = gen()
                changed = True
        if changed:
            self.save_config()
        self.client_id = self.config["client_id"]
        self.stats.setdefault("first_run", time.time())

    # ---------- saving ----------
    def save_config(self):
        with self.lock:
            _save(self.p_config, self.config, private=True)

    def save(self):
        with self.lock:
            _save(self.p_learned, self.learned)
            _save(self.p_stats, self.stats)

    # ---------- accounts / devices ----------
    def set_accounts(self, names, plextv_ids):
        with self.lock:
            self.stats["account_names"] = names          # server account id -> name
            self.stats["plextv_ids"] = plextv_ids        # server account id -> plex.tv user id

    def account_for_name(self, name):
        for a, n in self.stats.get("account_names", {}).items():
            if n == name:
                return a
        return None

    def note_players(self, players):
        """players from /status/sessions: [(acct, ip, {title, product, platform, model, cid})]"""
        if not players:
            return
        now = time.time()
        with self.lock:
            devs = self.stats.setdefault("devices", {})
            ipdev = self.stats.setdefault("ipdev", {})
            for acct, ip, p in players:
                key = "cid:" + p["cid"] if p.get("cid") else "ip:" + ip
                d = devs.setdefault(acct, {}).setdefault(key, {})
                d.update({k: p.get(k) for k in ("title", "product", "platform", "model")}, last_seen=now)
                if ip:
                    ipdev[f"{acct}|{ip}"] = key
                    d["ip"] = ip

    def note_device_catalog(self, catalog):
        """catalog: {client id: {name, platform}} from Plex's own device list"""
        if catalog:
            with self.lock:
                self.stats["devcat"] = catalog

    def entry_device(self, acct, e):
        """Device key of a learned request: the Plex device that made it when known, else its address."""
        m = self.learned.get("meta", {}).get(acct, {}).get(e, {})
        if m.get("dev"):
            return "cid:" + m["dev"]
        return self.device_for(acct, m.get("ip"))

    def device_for(self, acct, ip):
        if not ip:
            return "unknown"
        return self.stats.get("ipdev", {}).get(f"{acct}|{ip}", "ip:" + ip)

    def device_label(self, acct, key):
        d = self.stats.get("devices", {}).get(acct, {}).get(key)
        if d and d.get("title"):
            extra = d.get("product") or d.get("platform") or ""
            return d["title"], extra.replace("Plex for ", ""), True
        if key.startswith("cid:"):
            c = self.stats.get("devcat", {}).get(key[4:])
            if c and c.get("name"):
                return c["name"], c.get("platform") or "", True
        if key.startswith("ip:"):
            return f"Device at {key[3:]}", "not seen playing yet", False
        return "Unknown device", "", False

    # ---------- learning ----------
    def merge_learned(self, seen, keep_days, max_per, drop_ips=(), devices=None):
        """seen: {acct: {entry_json: ip}}, devices: {acct: {entry_json: client id}} where Plex's device
        statistics identified the device. Entries last seen from one of drop_ips (our own address) are removed."""
        now = time.time()
        with self.lock:
            accts = self.learned.setdefault("accounts", {})
            meta = self.learned.setdefault("meta", {})
            for acct in list(meta):
                for e, m in list(meta[acct].items()):
                    if m.get("ip") in drop_ips:
                        meta[acct].pop(e, None)
                        accts.get(acct, {}).pop(e, None)
            for acct, entries in seen.items():
                for e, ip in entries.items():
                    accts.setdefault(acct, {})[e] = now
                    if ip:
                        old = meta.get(acct, {}).get(e, {})
                        meta.setdefault(acct, {})[e] = dict({"dev": old["dev"]} if old.get("dev") else {}, ip=ip)
            for acct, entries in (devices or {}).items():
                for e, dev in entries.items():
                    if dev and e in accts.get(acct, {}):
                        meta.setdefault(acct, {}).setdefault(e, {})["dev"] = dev
            for acct in list(accts):
                ent = accts[acct]
                for e, ts in list(ent.items()):
                    if now - ts > keep_days * 86400:
                        del ent[e]
                if len(ent) > max_per:
                    for e in sorted(ent, key=ent.get)[:-max_per]:
                        del ent[e]
                if not ent:
                    del accts[acct]
                    meta.pop(acct, None)
                elif acct in meta:
                    meta[acct] = {e: v for e, v in meta[acct].items() if e in ent}
            self.learned["last_learn"] = now
            self.save()

    def entry_ip(self, acct, e):
        return self.learned.get("meta", {}).get(acct, {}).get(e, {}).get("ip")

    def note_activity(self, events, newest_ts):
        """events: [(acct, iso_ts)] of real hub requests -> hour-of-week usage per account (for smart mode)"""
        with self.lock:
            act = self.stats.setdefault("activity", {})
            for acct, ts in events:
                try:
                    dt = datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S")
                except ValueError:
                    continue
                a = act.setdefault(acct, {"how": [0] * 168, "last": 0})
                a["how"][dt.weekday() * 24 + dt.hour] += 1
                a["last"] = max(a["last"], dt.timestamp())
            if newest_ts:
                self.stats["activity_last_ts"] = newest_ts

    def note_logs(self, hub_lines, hit):
        with self.lock:
            self.stats["hubcache_lines"] = hub_lines
            self.stats["debug_logging_ok"] = hub_lines > 0
            if hit:
                self.stats["plex_hub_entries"], self.stats["plex_hit_ratio"] = hit
            self.save()

    def note_probe(self, seconds):
        with self.lock:
            self.stats["last_probe"] = {"ts": time.time(), "seconds": round(seconds, 3)}

    # ---------- real users' row loads ----------
    def add_loads(self, loads, newest_ts):
        """loads: [(iso_ts, ms, acct or None, device key, screen)] (only log lines newer than last time)"""
        if not loads:
            if newest_ts:                    # poster requests may have advanced the clock on their own
                with self.lock:
                    self.stats["loads_last_ts"] = max(newest_ts, self.stats.get("loads_last_ts", ""))
            return
        with self.lock:
            s = self.stats
            hourly, per_acct, per_dev, per_scr = (s.setdefault(k, {}) for k in
                                                  ("loads_hourly", "loads_acct", "loads_dev", "loads_screen"))
            tot = s.setdefault("loads_total", {"count": 0, "sum_ms": 0, "le": {str(b): 0 for b in LOAD_BUCKETS_MS}})
            for ts, ms, acct, dev, what in loads:
                _add_load(hourly.setdefault(ts[:13], _new_load()), ms)
                _add_load(per_scr.setdefault(what, {}).setdefault(ts[:10], _new_load()), ms)
                if acct:
                    _add_load(per_acct.setdefault(acct, {}).setdefault(ts[:10], _new_load()), ms)
                    _add_load(per_dev.setdefault(f"{acct}|{dev}", {}).setdefault(ts[:10], _new_load()), ms)
                tot["count"] += 1
                tot["sum_ms"] += ms
                for b in LOAD_BUCKETS_MS:
                    if ms <= b:
                        tot["le"][str(b)] += 1
            s["loads_last_ts"] = newest_ts
            self._prune()
            self.save()

    # ---------- posters ----------
    def note_photo_profiles(self, seen, keep_days, max_per=3):
        """seen: {acct: {profile json: times seen in this log read}} - how each account's apps ask for
        posters (size, URL form). The most used forms are kept: one-off sizes (episode stills, artwork)
        would otherwise multiply the work."""
        now = time.time()
        with self.lock:
            prof = self.stats.setdefault("photo_profiles", {})
            for acct, ps in seen.items():
                for p, n in ps.items():
                    rec = prof.setdefault(acct, {}).get(p)
                    rec = rec if isinstance(rec, dict) else {"n": 0}
                    prof[acct][p] = {"t": now, "n": max(rec["n"], n)}       # the log is re-read: keep the peak
            for acct in list(prof):
                keep = {p: r for p, r in prof[acct].items() if isinstance(r, dict) and now - r["t"] < keep_days * 86400}
                prof[acct] = dict(sorted(keep.items(), key=lambda kv: -kv[1]["n"])[:max_per])
                if not prof[acct]:
                    del prof[acct]

    def add_photo_loads(self, loads):
        """loads: [(iso ts, ms)] - real apps' poster requests. instant = Plex already had the resized image."""
        if not loads:
            return
        with self.lock:
            hourly = self.stats.setdefault("photo_hourly", {})
            for ts, ms in loads:
                h = hourly.setdefault(ts[:13], {"n": 0, "instant": 0, "slow": 0, "sum_ms": 0})
                h["n"] += 1
                h["sum_ms"] += ms
                if ms <= POSTER_INSTANT_MS:
                    h["instant"] += 1
                elif ms > POSTER_SLOW_MS:
                    h["slow"] += 1

    def note_posters(self, n, built, built_s, errors, queued):
        with self.lock:
            p = self.stats.setdefault("posters", {"requests": 0, "built": 0, "built_seconds": 0.0, "errors": 0})
            p["requests"] += n
            p["built"] += built
            p["built_seconds"] = round(p["built_seconds"] + built_s, 1)
            p["errors"] += errors
            p["queued"] = queued
            day = self.stats.setdefault("posters_daily", {})
            d = day.setdefault(datetime.date.today().isoformat(), {"requests": 0, "built": 0})
            d["requests"] += n
            d["built"] += built
            d["built_seconds"] = round(d.get("built_seconds", 0) + built_s, 1)
            for k in sorted(day)[:-8]:
                del day[k]
            self.save()

    # ---------- did real users get cached rows? ----------
    def add_hits(self, hits, slow_ms=1000):
        """hits: [(iso_ts, acct, 'hit' or the cause of the rebuild, ms, rows rebuilt, url, what, [[hub, ms]..])]
        - real users only. Per hour: n = loads, hit = served entirely from cache, slow = loads over slow_ms,
        c = slow loads per cause, w = their total ms per cause. Only slow loads are listed individually."""
        if not hits:
            return
        with self.lock:
            s = self.stats
            hourly = s.setdefault("hits_hourly", {})
            tot = s.setdefault("hits_total", {"n": 0, "hit": 0, "c": {}})
            recent = s.setdefault("misses", [])
            for ts, acct, why, ms, rebuilt, url, what, top in sorted(hits, key=lambda h: h[0]):
                cause = "not cache related" if why == "hit" else why
                for rec in (hourly.setdefault(ts[:13], {"n": 0, "hit": 0, "c": {}}), tot):
                    rec["n"] += 1
                    if why == "hit":
                        rec["hit"] += 1
                    if ms >= slow_ms:
                        rec["slow"] = rec.get("slow", 0) + 1
                        rec["c"][cause] = rec["c"].get(cause, 0) + 1
                        w = rec.setdefault("w", {})
                        w[cause] = w.get(cause, 0) + ms
                if ms >= slow_ms:
                    recent.append({"ts": ts, "acct": acct, "why": cause, "ms": ms, "rows": rebuilt, "url": url,
                                   "what": what, "top": top})
            s["misses"] = recent[-60:]

    def set_rows(self, rows):
        """rows: {hub id: [builds, total ms, max ms]} from the Plex logs read just now (a rolling snapshot)"""
        if not rows:
            return
        with self.lock:
            self.stats["rows"] = {h: {"n": n, "avg_ms": round(t / n), "max_ms": mx} for h, (n, t, mx) in rows.items()}
            self.stats["rows_ts"] = time.time()

    # ---------- warm runs ----------
    def note_run(self, n, cold, cold_s, errors, took, per_acct, warmed_accts, full=True, n_accounts=None):
        """warmed_accts: accounts whose every request was replayed (their idle-refresh clock restarts)"""
        now = time.time()
        with self.lock:
            s = self.stats
            tot = s.setdefault("totals", {"runs": 0, "requests": 0, "cold": 0, "cold_seconds": 0.0,
                                          "errors": 0, "busy_seconds": 0.0})
            for k, v in (("runs", 1), ("requests", n), ("cold", cold), ("cold_seconds", cold_s),
                         ("errors", errors), ("busy_seconds", took)):
                tot[k] = tot.get(k, 0) + v
            s.setdefault("runs", []).append({"ts": now, "n": n, "cold": cold, "cold_s": round(cold_s, 2),
                                             "errors": errors, "took": round(took, 1),
                                             "accounts": len(warmed_accts) if n_accounts is None else n_accounts})
            last = s.setdefault("last", {})
            for acct, ent in per_acct.items():
                for e, (sec, was_cold) in ent.items():
                    last.setdefault(acct, {})[e] = {"s": sec, "cold": was_cold, "ts": now}
            alw = self.learned.setdefault("acct_last_warm", {})
            for a in warmed_accts:
                alw[a] = now
            if full:
                self.learned["last_warm"] = now
            self._prune()
            self.save()

    def _prune(self):
        cutoff = time.time() - float(self.config.get("settings", {}).get("history_days", C.HISTORY_DAYS)) * 86400
        hour = datetime.datetime.fromtimestamp(cutoff).strftime("%Y-%m-%dT%H")
        s = self.stats
        s["runs"] = [r for r in s.get("runs", []) if r["ts"] >= cutoff][-5000:]
        s["loads_hourly"] = {k: v for k, v in s.get("loads_hourly", {}).items() if k >= hour}
        s["hits_hourly"] = {k: v for k, v in s.get("hits_hourly", {}).items() if k >= hour}
        s["photo_hourly"] = {k: v for k, v in s.get("photo_hourly", {}).items() if k >= hour}
        for k in ("loads_acct", "loads_dev", "loads_screen"):
            for who, days in list(s.get(k, {}).items()):
                s[k][who] = {d: v for d, v in days.items() if d >= hour[:10]}
                if not s[k][who]:
                    del s[k][who]
        known = self.learned.get("accounts", {})
        for acct in list(s.get("last", {})):
            if acct not in known:
                del s["last"][acct]
            else:
                s["last"][acct] = {e: v for e, v in s["last"][acct].items() if e in known[acct]}
