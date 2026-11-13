"""Sign in with Plex (the plex.tv PIN flow Tautulli/Overseerr use) + signed session cookies.

Roles: "owner" = the server owner (everything, setup, Warm now); "friend" = an account the server is shared
with (sees overall stats and only their own coverage). Anyone else is refused."""
import base64, hashlib, hmac, json, time, urllib.parse, urllib.request

from . import __version__, config as C

COOKIE = "hubwarmer_session"


def _plextv(method, url, client_id, token=None, data=None):
    h = {"Accept": "application/json", "X-Plex-Product": "Hub Warmer", "X-Plex-Version": __version__,
         "X-Plex-Client-Identifier": client_id}
    if token:
        h["X-Plex-Token"] = token
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=h, method=method), timeout=20) as r:
        return json.loads(r.read() or b"{}")


def start_pin(client_id):
    """-> (pin_id, code)"""
    p = _plextv("POST", "https://plex.tv/api/v2/pins", client_id, data={"strong": "true"})
    return p["id"], p["code"]


def auth_url(client_id, code, forward_url):
    q = urllib.parse.urlencode({"clientID": client_id, "code": code, "forwardUrl": forward_url,
                                "context[device][product]": "Hub Warmer"})
    return "https://app.plex.tv/auth#?" + q


def check_pin(client_id, pin_id):
    """-> plex.tv auth token once the user approved, else None"""
    p = _plextv("GET", f"https://plex.tv/api/v2/pins/{int(pin_id)}", client_id)
    return p.get("authToken")


def plextv_user(client_id, token):
    u = _plextv("GET", "https://plex.tv/api/v2/user", client_id, token=token)
    return {"id": str(u["id"]), "username": u.get("username") or u.get("title"),
            "title": u.get("title") or u.get("username"), "thumb": u.get("thumb", "")}


def owns_server(client_id, token, machine_id):
    res = _plextv("GET", "https://plex.tv/api/v2/resources?includeHttps=1", client_id, token=token)
    return any(r.get("clientIdentifier") == machine_id and r.get("owned") for r in res)


# ---------- sessions ----------
def _sign(secret, payload):
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def make_session(secret, user, role, days=None):
    data = {"uid": user["id"], "name": user["title"], "user": user["username"], "thumb": user.get("thumb", ""),
            "role": role, "exp": int(time.time() + (days or C.SESSION_DAYS) * 86400)}
    payload = base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode())
    return payload.decode() + "." + _sign(secret, payload)


def read_session(secret, cookie_header):
    if not cookie_header:
        return None
    for part in cookie_header.split(";"):
        k, _, v = part.strip().partition("=")
        if k != COOKIE or "." not in v:
            continue
        payload, sig = v.rsplit(".", 1)
        if not hmac.compare_digest(sig, _sign(secret, payload.encode())):
            return None
        try:
            data = json.loads(base64.urlsafe_b64decode(payload.encode()))
        except ValueError:
            return None
        return data if data.get("exp", 0) > time.time() else None
    return None
