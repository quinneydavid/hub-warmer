"""Web page, JSON API, Prometheus metrics and Sign in with Plex. Standard library only."""
import datetime, hmac, json, os, secrets, threading, time, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__, auth, config as C, metrics
from . import settings
from .core import describe, describe_row
from .store import load_summary, merge_loads, log

UI = open(os.path.join(os.path.dirname(__file__), "ui.html"), encoding="utf-8").read()
PIN_COOKIE = "hubwarmer_pin"
MAX_PINS = 50                               # sign-ins under way at once


def cookie_value(header, name):
    for part in (header or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return v
    return ""


def _days(n=7):
    today = datetime.date.today()
    return [(today - datetime.timedelta(days=i)).isoformat() for i in range(n - 1, -1, -1)]


def _series(per_day):
    return [dict(day=d, **load_summary(per_day.get(d, {}))) for d in _days()]


def build_status(store, warmer, me):
    owner = me["role"] == "owner"
    my_acct = "1" if owner else me["uid"]
    S = warmer.S
    with store.lock:
        s, L = store.stats, store.learned
        names = s.get("account_names", {})
        runs = s.get("runs", [])
        day0 = datetime.datetime.combine(datetime.date.today(), datetime.time()).timestamp()
        today_runs = [r for r in runs if r["ts"] >= day0]
        hourly = s.get("loads_hourly", {})
        by_day = {}
        for hour, rec in hourly.items():
            by_day.setdefault(hour[:10], []).append(rec)
        daily = _series({d: merge_loads(v) for d, v in by_day.items()})
        saved_day = {}
        for r in runs:
            d = datetime.date.fromtimestamp(r["ts"]).isoformat()
            saved_day[d] = saved_day.get(d, 0) + r["cold_s"]
        for row in daily:
            row["saved_s"] = round(saved_day.get(row["day"], 0), 1)
        hours = [dict(hour=h, **load_summary(hourly[h])) for h in sorted(hourly)[-48:]]
        today = load_summary(merge_loads(by_day.get(datetime.date.today().isoformat(), [])))
        off_a, off_d = S.disabled("accounts"), S.disabled("devices")
        why = warmer.last_plan.get("why", {})
        accounts = []
        for acct, ent in sorted(L.get("accounts", {}).items(), key=lambda kv: names.get(kv[0], kv[0]).lower()):
            if not owner and acct != my_acct:
                continue
            last = s.get("last", {}).get(acct, {})
            devs = {}
            for e, seen_ts in ent.items():
                url, lang, feat = json.loads(e)
                dev = store.entry_device(acct, e)
                what, app, lng = describe(url, lang, feat, warmer.sections)
                r = last.get(e, {})
                devs.setdefault(dev, []).append({"what": what, "app": app, "lang": lng, "seen": round(seen_ts),
                                                 "last_s": r.get("s"), "was_cold": r.get("cold"),
                                                 "warmed": round(r["ts"]) if r else None})
            dev_list = []
            for key, reqs in sorted(devs.items()):
                title, extra, named = store.device_label(acct, key)
                dev_list.append({"key": key, "title": title, "extra": extra, "named": named,
                                 "enabled": f"{acct}|{key}" not in off_d,
                                 "requests": sorted(reqs, key=lambda r: r["what"]),
                                 "daily": _series(s.get("loads_dev", {}).get(f"{acct}|{key}", {}))})
            act = s.get("activity", {}).get(acct, {})
            accounts.append({"id": acct, "name": names.get(acct, f"account {acct}"), "enabled": acct not in off_a,
                             "reason": why.get(acct, ""), "devices": dev_list,
                             "daily": _series(s.get("loads_acct", {}).get(acct, {})),
                             "usual": act.get("how") if owner else None,
                             "last_active": round(act.get("last", 0)) or None})
        last_warm = L.get("last_warm", 0)
        hh = s.get("hits_hourly", {})
        tday = datetime.date.today().isoformat()

        def hit_sum(recs, **extra):
            n, hit, causes, waits = sum(r["n"] for r in recs), sum(r["hit"] for r in recs), {}, {}
            extra["slow"] = sum(r.get("slow", 0) for r in recs)
            for r in recs:
                for c, v in r["c"].items():
                    causes[c] = causes.get(c, 0) + v
                for c, v in r.get("w", {}).items():
                    waits[c] = waits.get(c, 0) + v
            return dict(extra, n=n, hit=hit, pct=round(100 * hit / n, 1) if n else None, causes=causes, waits=waits)
        hits = {"today": hit_sum([v for k, v in hh.items() if k.startswith(tday)]), "week": hit_sum(list(hh.values())),
                "daily": [hit_sum([v for k, v in hh.items() if k.startswith(d)], day=d) for d in _days()]}
        screens = sorted((dict(screen=k, **load_summary(merge_loads(v.values()))) for k, v in s.get("loads_screen", {}).items()),
                         key=lambda r: -r["n"])
        ph, pd = s.get("photo_hourly", {}), s.get("posters_daily", {})
        prof = s.get("photo_profiles", {})

        def photo_day(d):
            recs = [v for k, v in ph.items() if k.startswith(d)]
            n = sum(r["n"] for r in recs)
            inst, slow = sum(r["instant"] for r in recs), sum(r["slow"] for r in recs)
            return {"day": d, "n": n, "instant": inst, "slow": slow, "between": n - inst - slow,
                    "pct": round(100 * inst / n, 1) if n else None,
                    "avg_ms": round(sum(r["sum_ms"] for r in recs) / n) if n else None,
                    "checked": pd.get(d, {}).get("requests", 0), "prepared": pd.get(d, {}).get("built", 0),
                    "prepare_s": pd.get(d, {}).get("built_seconds", 0)}
        posters = dict(s.get("posters", {}), today=pd.get(tday, {}), daily=[photo_day(d) for d in _days()],
                       hourly=[dict(hour=h, **ph[h]) for h in sorted(ph)[-48:]],
                       accounts=len(prof),
                       shared_forms=sum(1 for v in prof.values() for k in v if not json.loads(k)["token"]),
                       user_forms=sum(1 for v in prof.values() for k in v if json.loads(k)["token"]),
                       enabled=bool(S.get("warm_posters")))
        work = {}
        for r in runs:
            if r["ts"] >= time.time() - 48 * 3600:
                w = work.setdefault(datetime.datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%dT%H"),
                                    {"n": 0, "cold": 0, "cold_s": 0.0, "took": 0.0})
                w["n"] += r["n"]
                w["cold"] += r["cold"]
                w["cold_s"] = round(w["cold_s"] + r["cold_s"], 1)
                w["took"] = round(w["took"] + r["took"], 1)
        out = {
            "version": __version__, "me": me, "state": warmer.state,
            "error": warmer.last_error if owner else ("error" if warmer.last_error else ""),
            "setup_needed": warmer.state == "waiting-for-setup",
            "need_address": not S.get("plex_url"),
            "debug_logging_ok": s.get("debug_logging_ok"),
            "first_run": round(s.get("first_run", 0)),
            "last_warm": round(last_warm),
            "last_probe": s.get("last_probe"),
            "plex": {"hit_ratio": s.get("plex_hit_ratio"), "entries": s.get("plex_hub_entries")},
            "hits": hits,
            "screens": screens,
            "posters": posters,
            "slow_s": S.get("slow_load_seconds"),
            "watching": len(warmer.playing),
            "saved": {"today": round(sum(r["cold_s"] for r in today_runs), 1),
                      "week": round(sum(r["cold_s"] for r in runs), 1),
                      "total": round(s.get("totals", {}).get("cold_seconds", 0), 1)},
            "loads": {"today": today, "daily": daily, "hourly": hours},
            "accounts": accounts,
            "counts": {"accounts": len(L.get("accounts", {})),
                       "requests": sum(len(v) for v in L.get("accounts", {}).values())},
            "now": round(time.time()),
        }
        if owner:
            out["runs"] = runs[-60:]
            out["misses"] = [dict(m, name=names.get(m["acct"], m["acct"]),
                                  what=m.get("what") or describe(m["url"], "", "x", warmer.sections)[0],
                                  top=[[describe_row(h, warmer.sections, warmer.collections)[0], t]
                                       for h, t in m.get("top", [])])
                             for m in reversed(s.get("misses", [])) if m["ms"] >= S.get("slow_load_seconds") * 1000]
            out["work"] = [dict(hour=h, **w) for h, w in sorted(work.items())]
            rows, libs = [], {}
            for hub, r in s.get("rows", {}).items():
                title, lib, kind = describe_row(hub, warmer.sections, warmer.collections)
                rows.append({"row": title, "library": lib, "kind": kind, "avg_ms": r["avg_ms"], "max_ms": r["max_ms"],
                             "n": r["n"]})
                if lib:
                    libs[lib] = libs.get(lib, 0) + r["avg_ms"]
            out["rows"] = {"top": sorted(rows, key=lambda r: -r["avg_ms"])[:25],
                           "libraries": [{"library": k, "cold_ms": v} for k, v in sorted(libs.items(), key=lambda kv: -kv[1])],
                           "ts": round(s.get("rows_ts", 0)) or None}
            out["schedule"] = {
                "mode": S.get("mode"),
                "next_check": round(warmer.next_check) or None,
                "next_top_up": round(last_warm + S.get("top_up_minutes") * 60) if last_warm else None,
                "next_learn": round(L.get("last_learn", 0) + S.get("learn_minutes") * 60) if S.get("plex_logs") == "api" else None,
                "plan": {"requests": warmer.last_plan.get("requests"), "accounts": warmer.last_plan.get("accounts"),
                         "why": {names.get(a, a): r for a, r in why.items()}},
            }
            out["settings"] = S.describe()
            out["fixed"] = {"DATA_DIR": C.DATA_DIR, "WEB_PORT": C.WEB_PORT, "WEB_BIND": C.WEB_BIND, "WEB_AUTH": C.WEB_AUTH,
                            "PLEX_TOKEN": "set" if C.PLEX_TOKEN else "not set (the owner signs in on this page)"}
            out["self_ips_found"] = sorted(s.get("self_ips", {}))
        return out


class App:
    def __init__(self, store, warmer):
        self.store, self.warmer = store, warmer
        self.pins = {}                      # pin id -> (created, browser nonce, Plex address shown): pending sign-ins
        self.lock = threading.Lock()

    def setup_open(self):
        """True until an owner token exists. Only then may the Plex address be set without signing in."""
        return not self.warmer.owner_token()

    def set_address(self, url):
        """Test and store the Plex address. -> (errors, what answered)"""
        w = self.warmer
        errors = w.S.update({"plex_url": url})
        if errors or not url:
            return errors or ["Plex Media Server address: enter an address"], None
        machine, version = settings.probe_plex(w.S.get("plex_url"))
        w.machine_id, w.server_version, w.owner_info = "", "", None
        w.wake.set()
        return [], {"machine": machine, "version": version}

    def role_for(self, user, token):
        """owner / friend / None. First owner sign-in completes setup (stores the owner token)."""
        w, st = self.warmer, self.store
        otoken = w.owner_token()
        if not otoken:
            machine = settings.probe_plex(w.S.get("plex_url"))[0]
            if not auth.owns_server(st.client_id, token, machine):
                return None
            with st.lock:
                st.config["owner_token"] = token
                st.save_config()
            w.owner_info = None
            log(f"setup complete: owner {user['username']} signed in")
            w.warm_now()
            return "owner"
        if user["id"] == w.owner(otoken)["id"]:
            return "owner"
        if user["id"] not in w.shared_user_ids():
            w.account_tokens(otoken, max_age=0)          # refresh the shared list once
        return "friend" if user["id"] in w.shared_user_ids() else None


def make_handler(app):
    class H(BaseHTTPRequestHandler):
        server_version = f"hub-warmer/{__version__}"

        def log_message(self, *a):
            pass

        def send(self, code, body, ctype="application/json", headers=None):
            data = body if isinstance(body, bytes) else body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Cache-Control", "no-store")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def base_url(self):
            if app.warmer.S.get("public_url"):
                return app.warmer.S.get("public_url")
            proto = self.headers.get("X-Forwarded-Proto", "http").split(",")[0].strip()
            host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host", f"localhost:{C.WEB_PORT}")
            return f"{proto}://{host}"

        def me(self):
            if C.WEB_AUTH == "none":
                return {"uid": "1", "name": "local", "user": "local", "thumb": "", "role": "owner"}
            return auth.read_session(app.store.config["secret"], self.headers.get("Cookie"))

        def redirect(self, to, headers=None):
            self.send(302, b"", "text/plain", dict({"Location": to}, **(headers or {})))

        def body_json(self):
            n = int(self.headers.get("Content-Length") or 0)
            if not 0 < n < 65536:
                return {}
            try:
                return json.loads(self.rfile.read(n))
            except ValueError:
                return {}

        def do_GET(self):
            url = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(url.query)
            if url.path == "/healthz":
                return self.send(200, "ok", "text/plain")
            if url.path == "/metrics":
                if not app.warmer.S.get("metrics_public") and not self.me():
                    return self.send(401, "sign in required", "text/plain")
                return self.send(200, metrics.render(app.store, app.warmer), "text/plain; version=0.0.4")
            if url.path in ("/", "/index.html"):
                return self.send(200, UI, "text/html; charset=utf-8")
            if url.path == "/api/status":
                me = self.me()
                if not me:
                    return self.send(401, json.dumps({"signin": True, "setup_needed": app.setup_open(),
                                                      "need_address": app.setup_open() and not app.warmer.S.get("plex_url"),
                                                      "plex_url": app.warmer.S.get("plex_url") if app.setup_open() else ""}))
                return self.send(200, json.dumps(build_status(app.store, app.warmer, me)))
            if url.path == "/login":
                with app.lock:
                    app.pins = {k: v for k, v in app.pins.items() if time.time() - v[0] < 900}
                    if len(app.pins) >= MAX_PINS:        # /login needs no sign-in: bound what it can make plex.tv do
                        return self.redirect("/?e=busy")
                pin_id, code = auth.start_pin(app.store.client_id)
                nonce = secrets.token_urlsafe(24)
                with app.lock:
                    # the sign-in belongs to this browser (nonce cookie) and, during setup, to the address it was shown
                    app.pins[str(pin_id)] = (time.time(), nonce, app.warmer.S.get("plex_url"))
                secure = "; Secure" if self.base_url().startswith("https") else ""
                return self.redirect(auth.auth_url(app.store.client_id, code,
                                                   f"{self.base_url()}/auth/callback?pin={pin_id}"),
                                     {"Set-Cookie": f"{PIN_COOKIE}={nonce}; Path=/auth; HttpOnly; SameSite=Lax; "
                                                    f"Max-Age=900{secure}"})
            if url.path == "/auth/callback":
                pin = (q.get("pin") or [""])[0]
                with app.lock:
                    ok = app.pins.pop(pin, None)
                if not ok or not hmac.compare_digest(ok[1], cookie_value(self.headers.get("Cookie"), PIN_COOKIE)):
                    return self.redirect("/?e=expired")
                if app.setup_open() and ok[2] != app.warmer.S.get("plex_url"):
                    return self.redirect("/?e=changed")  # the address was changed while this sign-in was under way
                token = None
                for _ in range(10):                      # plex.tv may need a moment after the approval
                    token = auth.check_pin(app.store.client_id, pin)
                    if token:
                        break
                    time.sleep(1)
                if not token:
                    return self.redirect("/?e=cancelled")
                user = auth.plextv_user(app.store.client_id, token)
                try:
                    role = app.role_for(user, token)
                except Exception as e:
                    log(f"sign-in check failed for {user['username']}: {e!r}")
                    return self.redirect("/?e=check")
                if not role:
                    log(f"sign-in refused: {user['username']} has no access to this server")
                    return self.redirect("/?e=denied")
                secure = "; Secure" if self.base_url().startswith("https") else ""
                days = app.warmer.S.get("session_days")
                cookie = (f"{auth.COOKIE}={auth.make_session(app.store.config['secret'], user, role, days)}; Path=/; "
                          f"HttpOnly; SameSite=Lax; Max-Age={int(days * 86400)}{secure}")
                log(f"signed in: {user['username']} ({role})")
                return self.redirect("/", {"Set-Cookie": cookie})
            return self.send(404, "not found", "text/plain")

        def do_POST(self):
            url = urllib.parse.urlparse(self.path)
            if self.headers.get("X-Hub-Warmer") != "1":          # CSRF: only our own page's fetch() sends this header
                return self.send(403, json.dumps({"error": "missing header"}))
            if url.path == "/logout":
                return self.send(200, "{}", headers={"Set-Cookie": f"{auth.COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"})
            me = self.me()
            if url.path == "/api/setup":                 # first run: the Plex address, before anyone can sign in
                if not (app.setup_open() or (me and me["role"] == "owner")):
                    return self.send(403, json.dumps({"error": "setup is complete; the owner changes this under Settings"}))
                errors, found = app.set_address(str(self.body_json().get("plex_url", "")).strip())
                if errors:
                    return self.send(400, json.dumps({"errors": errors}))
                log(f"Plex address set to {app.warmer.S.get('plex_url')}")
                return self.send(200, json.dumps(dict(found, ok=True)))
            if not me:
                return self.send(401, json.dumps({"signin": True}))
            if me["role"] != "owner":
                return self.send(403, json.dumps({"error": "owner only"}))
            w = app.warmer
            if url.path == "/api/warm":
                w.warm_now()
                return self.send(202, json.dumps({"ok": True}))
            if url.path == "/api/settings":
                changes = self.body_json().get("changes", {})
                errors = w.S.update(changes)
                if errors:
                    return self.send(400, json.dumps({"errors": errors}))
                if "plex_url" in changes:                # another server may answer there now
                    w.machine_id, w.server_version, w.owner_info = "", "", None
                log(f"settings changed by {me['user']}")
                w.wake.set()
                return self.send(200, json.dumps({"ok": True}))
            if url.path == "/api/toggle":
                b = self.body_json()
                kind, ident, enabled = b.get("kind"), str(b.get("id", "")), bool(b.get("enabled"))
                if kind not in ("accounts", "devices") or not ident:
                    return self.send(400, json.dumps({"error": "bad request"}))
                w.S.set_enabled(kind, ident, enabled)
                log(f"{me['user']} turned warming {'on' if enabled else 'off'} for {kind[:-1]} {ident}")
                return self.send(200, json.dumps({"ok": True}))
            return self.send(404, json.dumps({"error": "not found"}))

    return H


def serve(store, warmer, stop):
    if not C.WEB_PORT:
        return None
    srv = ThreadingHTTPServer((C.WEB_BIND, C.WEB_PORT), make_handler(App(store, warmer)))
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log(f"web page + /metrics on port {C.WEB_PORT} (auth: {C.WEB_AUTH})")
    return srv
