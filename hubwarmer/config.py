"""Start-up values from environment variables. Only DATA_DIR, WEB_PORT, WEB_BIND, WEB_AUTH and PLEX_TOKEN are
environment-only; the rest are defaults for the Settings page (settings.py)."""
import os


def env(name, default):
    return os.environ.get(name, default)


def _set(name):
    return set(filter(None, (x.strip() for x in env(name, "").split(","))))


PLEX_URL = env("PLEX_URL", "").rstrip("/")              # optional default; else asked for on the web page
PLEX_TOKEN = env("PLEX_TOKEN", "")                       # optional; else sign in as owner on the web page
PLEX_PREFS = env("PLEX_PREFERENCES", "/plex/Preferences.xml")   # optional fallback for the owner token
PLEX_LOGS = env("PLEX_LOGS", "api")                      # "api" (/diagnostics/logs) or a Logs folder path
DATA_DIR = env("DATA_DIR", "/data")

INTERVAL = int(env("INTERVAL_SECONDS", "120"))           # probe interval
TOP_UP_MIN = float(env("TOP_UP_MINUTES", "5"))           # full warm at least this often
LEARN_MIN = float(env("LEARN_MINUTES", "10"))            # api mode: how often to download + read the logs
SLOW_S = float(env("PROBE_SLOW_SECONDS", "1.0"))         # probe slower than this = cache was dropped
COLD_S = float(env("COLD_SECONDS", "0.5"))               # a replay slower than this "was cold"
KEEP_DAYS = float(env("KEEP_DAYS", "3"))                 # forget an app's requests after this long unused
MAX_PER_ACCOUNT = int(env("MAX_REQUESTS_PER_ACCOUNT", "12"))   # most recently used are kept
PAUSE_ABOVE = int(env("PAUSE_ABOVE_TRANSCODES", "0"))    # 0 = never pause
SELF_IPS = _set("SELF_IPS")             # the address Plex sees this container's requests from
INCLUDE = _set("INCLUDE_USERS")
EXCLUDE = _set("EXCLUDE_USERS")

WEB_PORT = int(env("WEB_PORT", "8080"))                  # 0 = no web page / metrics
WEB_BIND = env("WEB_BIND", "0.0.0.0")
WEB_AUTH = env("WEB_AUTH", "plex")                       # "plex" (Sign in with Plex) or "none" (trusted network/proxy)
PUBLIC_URL = env("PUBLIC_URL", "").rstrip("/")           # optional: external URL for the Plex sign-in redirect
SESSION_DAYS = float(env("SESSION_DAYS", "30"))
METRICS_PUBLIC = env("METRICS_PUBLIC", "true").lower() in ("1", "true", "yes")   # /metrics without sign-in
HISTORY_DAYS = float(env("HISTORY_DAYS", "7"))
