"""Library changes from Plex's own notification feed (/:/eventsource/notifications).

Plex drops the cached rows of every account as soon as something is added, changed or removed. Without this
feed the warmer only notices at its next check; with it, a pass starts once the change has settled.
Plex also drops every account's collection rows when someone stops or finishes playing something; the same feed
reports that, and a shorter pass over the collection rows follows."""
import json, threading, time, urllib.request

from . import __version__
from .store import log

LIBRARY = "com.plexapp.plugins.library"
DONE, DELETED = 5, 9                     # timeline states: item processed / item removed
KINDS = {1: "movie", 2: "show", 3: "season", 4: "episode", 8: "artist", 9: "album", 10: "track", 18: "collection"}


def library_changes(event, data):
    """One notification -> [short description] of the library changes in it ([] = nothing that drops rows).
    Accepts the eventsource form ({"TimelineEntry": [...]}) and the websocket form
    ({"NotificationContainer": {"type": "timeline", "TimelineEntry": [...]}})."""
    if not isinstance(data, dict):
        return []
    if isinstance(data.get("NotificationContainer"), dict):
        data = data["NotificationContainer"]
        event = event or data.get("type", "")
    out = []
    entries = data.get("TimelineEntry")
    if entries is None and "state" in data and "type" in data:
        entries = [data]
    if isinstance(entries, dict):
        entries = [entries]
    for t in entries if isinstance(entries, list) else []:
        if not isinstance(t, dict) or t.get("identifier", LIBRARY) != LIBRARY:
            continue
        if t.get("state") not in (DONE, DELETED) or str(t.get("sectionID", "")) in ("", "-1"):
            continue
        kind = KINDS.get(t.get("type"), "item")
        out.append(f"{kind} {'removed' if t.get('state') == DELETED else 'updated'}"
                   + (f": {t['title']}" if t.get("title") else ""))
    acts = data.get("ActivityNotification")
    if acts is None and isinstance(data.get("Activity"), dict):
        acts = [data]
    if isinstance(acts, dict):
        acts = [acts]
    for a in acts if isinstance(acts, list) else []:
        act = a.get("Activity") if isinstance(a, dict) else None
        if isinstance(act, dict) and a.get("event") == "ended" and str(act.get("type", "")).startswith("library."):
            out.append(f"{act.get('title') or act['type']} finished")
    return out


PLAY_FORGET = 6 * 3600                   # a session not heard of for this long is forgotten


def playback_stops(event, data, states, now=None):
    """One notification -> number of playback sessions that just stopped. states = {session key: (state, when)},
    updated in place: a session counts once when it changes to "stopped", not on repeated stopped reports."""
    if not isinstance(data, dict):
        return 0
    if isinstance(data.get("NotificationContainer"), dict):
        data = data["NotificationContainer"]
    items = data.get("PlaySessionStateNotification")
    if isinstance(items, dict):
        items = [items]
    now = now or time.time()
    stops = 0
    for p in items if isinstance(items, list) else []:
        if not isinstance(p, dict) or not p.get("state"):
            continue
        key = str(p.get("sessionKey") or p.get("clientIdentifier") or p.get("ratingKey") or "")
        prev = states.get(key, ("", 0))[0]
        states[key] = (p["state"], now)
        if p["state"] == "stopped" and prev != "stopped":
            stops += 1
    if len(states) > 500:
        for k in [k for k, (_, t) in states.items() if now - t > PLAY_FORGET]:
            del states[k]
    return stops


def read_events(lines):
    """Server-sent event lines (bytes or str) -> (event name, parsed data) for each complete event."""
    event, data = "", []
    for raw in lines:
        line = (raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw).rstrip("\r\n")
        if line:
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].lstrip())
            continue
        if data:
            try:
                yield event, json.loads("\n".join(data))
            except ValueError:
                pass
        event, data = "", []


class LibraryEvents:
    """Listens to the feed in its own thread. The warm loop asks due() and starts a pass when it says so."""

    def __init__(self, warmer):
        self.w = warmer
        self.lock = threading.Lock()
        self.connected = False
        self.connected_since = 0.0
        self.pending, self.first, self.last = 0, 0.0, 0.0     # changes since the last library pass
        self.sample = []                                       # a few of those, for the log line
        self.last_change, self.last_change_text = 0.0, ""
        self.changes_total = 0
        self.passes_total = 0
        self.kinds_seen = set()                                # event names logged once, to show the feed works
        self.play_states = {}                                  # playback session -> (last state, when)
        self.play_pending, self.play_first, self.play_last = 0, 0.0, 0.0   # stops since the last playback pass
        self.play_pass_end = 0.0
        self.stops_total = 0
        self.play_passes_total = 0

    # ---------- feed ----------
    def start(self, stop):
        threading.Thread(target=self.run, args=(stop,), daemon=True, name="library-events").start()

    def run(self, stop):
        backoff, failed_before = 5, False
        while not stop.is_set():
            S = self.w.S
            otoken = self.w.owner_token()
            if not S.get("warm_on_library_change") or not otoken or not S.get("plex_url"):
                stop.wait(30)
                continue
            try:
                req = urllib.request.Request(
                    S.get("plex_url") + "/:/eventsource/notifications?filters=timeline,activity,playing",
                    headers={"X-Plex-Token": otoken, "Accept": "text/event-stream",
                             "X-Plex-Client-Identifier": self.w.store.client_id, "X-Plex-Product": "Hub Warmer",
                             "X-Plex-Version": __version__})
                with urllib.request.urlopen(req, timeout=300) as r:   # Plex pings well within 300 s
                    if not self.connected_since or failed_before:
                        log("library events: connected, a pass starts when the library changes")
                    self.connected, self.connected_since, backoff, failed_before = True, time.time(), 5, False
                    for event, data in read_events(r):
                        if stop.is_set() or not S.get("warm_on_library_change"):
                            break
                        changes = library_changes(event, data)
                        stops = playback_stops(event, data, self.play_states)
                        kind = (event or "-") + (" (library change)" if changes else " (playback stopped)" if stops
                                                 else "")
                        if kind not in self.kinds_seen and len(self.kinds_seen) < 20:
                            self.kinds_seen.add(kind)
                            log(f"library events: first '{kind}' event: {json.dumps(data)[:300]}")
                        self.note(changes)
                        if S.get("warm_on_playback_stop"):
                            self.note_stops(stops)
            except Exception as e:
                if not failed_before:
                    log(f"library events: feed lost ({e!r}); retrying, scheduled passes continue meanwhile")
                failed_before = True
            self.connected = False
            stop.wait(backoff)
            backoff = min(backoff * 2, 300)

    def note(self, changes):
        if not changes:
            return
        now = time.time()
        with self.lock:
            if not self.pending:
                self.first = now
            self.pending += len(changes)
            self.last = now
            self.changes_total += len(changes)
            self.last_change, self.last_change_text = now, changes[-1]
            if len(self.sample) < 3:
                self.sample += changes[:3 - len(self.sample)]

    def note_stops(self, n):
        if not n:
            return
        now = time.time()
        with self.lock:
            if not self.play_pending:
                self.play_first = now
            self.play_pending += n
            self.play_last = now
            self.stops_total += n

    # ---------- when to warm ----------
    def due(self, last_pass_end):
        """A pass is due once no change arrived for the settle time (a scan adds many items in a row), or at the
        latest five settle periods after the first change; never sooner than the settle time after a pass."""
        if not self.pending:
            return False
        settle, now = self.w.S.get("library_settle_seconds"), time.time()
        if now - last_pass_end < settle:
            return False
        return now - self.last >= settle or now - self.first >= 5 * settle

    def play_due(self, last_pass_end):
        """A collection pass is due once no further playback has stopped for the settle time (people often stop one
        thing and start the next), at most three settle periods after the first stop. Not sooner than the settle
        time after any pass, nor sooner than the minimum gap after the previous playback pass."""
        if not self.play_pending:
            return False
        S, now = self.w.S, time.time()
        settle = S.get("playback_settle_seconds")
        if now - last_pass_end < settle or now - self.play_pass_end < S.get("playback_min_gap_minutes") * 60:
            return False
        return now - self.play_last >= settle or now - self.play_first >= 3 * settle

    def take_play(self):
        """-> log label for the collection pass that starts now; clears the pending stops"""
        with self.lock:
            n, self.play_pending = self.play_pending, 0
            self.play_passes_total += 1
            self.play_pass_end = time.time()
        return f"playback stopped: {n} session{'s' if n != 1 else ''}"

    def done_play(self):
        """the collection pass has ended: the minimum gap counts from here"""
        self.play_pass_end = time.time()

    def take(self):
        """-> log label for the pass that starts now; clears the pending changes"""
        with self.lock:
            n, sample = self.pending, self.sample
            self.pending, self.sample = 0, []
            self.passes_total += 1
        more = f", +{n - len(sample)} more" if n > len(sample) else ""
        return f"library change: {'; '.join(s[:60] for s in sample)}{more}"

    def status(self):
        return {"enabled": bool(self.w.S.get("warm_on_library_change")), "connected": self.connected,
                "last_change": round(self.last_change) or None,
                "pending": self.pending, "changes": self.changes_total, "passes": self.passes_total,
                "playback": {"enabled": bool(self.w.S.get("warm_on_playback_stop")), "pending": self.play_pending,
                             "stops": self.stops_total, "passes": self.play_passes_total}}
