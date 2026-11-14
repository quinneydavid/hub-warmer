# How it works

This document describes what Plex caches, when the cache is discarded, and what the warmer does at each step.

## Plex's hub cache

Each row on a Plex Home screen or library page is a hub. Clients request hubs in batches:

| Screen | Request |
|---|---|
| Home | `GET /hubs/promoted?contentDirectoryID=…` (one per pinned library) |
| A library's Recommended page | `GET /hubs/sections/<id>?…` |
| Continue Watching | `GET /hubs/continueWatching?…` |

Building a hub takes one or more database queries. Plex keeps each built hub in an in-memory cache. With debug logging enabled, the server log records every cache operation:

```
HubCache: Computed '1/custom.collection.1.1003665/1/hubs/sections/1/enexternal-media,indirect-media,hub-style-list/count=12&excludeFields=summary' in 421 ms.
HubCache: Retrieving '1/home.continue/1/hubs/promoted//contentDirectoryID=1&count=20&excludeContinueWatching=1' from the cache.
HubCache: Expiring '1/custom.collection.1.1003665/…' because of event plex.event.play.session.state.update.
HubCache: 813 hubs cached, 59.9% hit ratio.
```

The cache key has this structure:

```
<account id> / <hub id> / <n> / <request path> / <X-Plex-Language><X-Plex-Features> / <query string>
```

An entry therefore belongs to one account, one exact request and one client's language and feature flags. The same row on a television, a phone and a browser is three entries.

Plex discards entries in these cases:

| Event | Entries discarded |
|---|---|
| Library change: new or removed items, metadata refresh, collection edit | All accounts |
| Playback state update for an account, about every 10 seconds during playback | That account's collection and progress rows |
| No use for 24 hours | The unused entry |

After entries are discarded, the next request rebuilds every hub on the page before it returns.

### Build cost

The log line `Computed … in N ms` gives the build time of each hub. On the reference server, each ordinary (non-smart) collection row took about 350 ms to build regardless of the number of items in it, because the query Plex uses for a collection's members scales with the size of the library. A page with twelve such rows took more than four seconds when uncached. Smart collections and most built-in rows were several times cheaper. The Overview page lists the measured cost of every row.

## What the warmer does

### 1. Learn

Every 10 minutes the warmer reads the Plex log, either through the Plex API (`/diagnostics/logs`, owner token) or from a mounted `Logs` folder. For every `HubCache` line that belongs to a real client request it records:

- the account id and the request (path and query) from the cache key,
- the client's language and feature flags from the cache key; these are request headers and do not appear in the request log,
- the address the request came from, matched through the request id.

Each account keeps its 12 most recently used requests. A request is dropped after 3 days without use. Both limits are configurable.

From the same log the warmer also records:

- the duration of every real hub request (`Completed … NNNms`), which is the source of the load time charts,
- for every real hub request, whether all its rows came from the cache, and if not, which rows were rebuilt and which event had discarded them,
- when each account uses Plex, per hour of the week, for smart mode,
- the build time of each hub.

### 2. Exclude its own requests

The warmer's replays appear in the Plex log like any other request and must not be learned. On each pass the warmer sends one marked request, `/identity?hubwarmer_self=<id>`, finds it in the log, and treats the address it came from as its own. This works on the Plex host, in Docker and on any Kubernetes node without configuration.

### 3. Detect

- **Library changes.** The warmer keeps a connection open to Plex's notification feed (`/:/eventsource/notifications?filters=timeline,activity`, as the owner). A timeline entry for the library in state 5 (processed) or 9 (removed), or a `library.*` activity that has ended, counts as a library change: new and removed items, metadata refreshes and collection edits. Changes usually arrive in bursts while a scan runs, so a pass starts once none has arrived for the settle time (default 30 seconds), at the latest five settle periods after the first, and never sooner than the settle time after the previous pass. If the feed is lost the warmer reconnects with increasing delays and relies on the probe and the schedule meanwhile.
- **Probe.** Every 2 minutes one hub request is timed. A slow response means the cache has been cleared and starts a pass. The probe is skipped while the owner is playing, because the owner's rows are discarded continuously during playback.
- **Devices.** The request log records only an address, so the warmer also reads Plex's per-second traffic statistics per account and device (`/statistics/bandwidth?timespan=6`, kept by Plex for a few minutes) every 30 seconds. A page request is attributed to the device on that account that was transferring data at that second. When two devices were both busy, the request keeps its address only.
- **Sessions.** Every 15 seconds `/status/sessions` is read. An account with an active session is skipped. When its session ends, the account is warmed immediately, starting with the requests from the device that was playing. Session data also provides device names.

### 4. Replay

A pass runs when the library has changed, when the probe is slow, when a session has ended, and at least every 5 minutes. In smart mode it replays the Home screen requests (`/hubs/promoted`, `/hubs/continueWatching`) of every account, and all learned requests of the accounts that are due:

- as that account; server access tokens for shared accounts come from plex.tv (`/api/servers/<id>/shared_servers`) and are held in memory only,
- with that client's `X-Plex-Language` and `X-Plex-Features` headers, so Plex fills the entry the client will read,
- one request at a time, most urgent accounts first, within the pass time budget.

A replay that takes longer than 0.5 seconds indicates that Plex had discarded the entry and rebuilt it. That time is reported as pre-build time.

### 5. Posters

Apps load posters through `/photo/:/transcode?width=…&height=…&url=…`. Plex resizes the image and keeps the result in its image cache, keyed on the exact parameters and on the `url` value. Some apps pass the poster as a relative path, and all users share that entry. Others pass an absolute URL that includes the user's token, and that entry is per user.

The warmer learns each account's poster request forms from the log. After a pass it takes the poster paths from the hub responses it just received and requests each one in those forms, one at a time, within the poster time budget. A poster path changes when the poster changes, so new and edited posters are picked up on the next pass. The owner's apps that embed their own device token cannot be prepared, because the warmer does not have those tokens.

## Measurement

All figures in the web interface and metrics come from the Plex log:

| Figure | Source |
|---|---|
| Load time | `Completed` line of each real hub request |
| Cache hit | A real request whose hubs were all `Retrieving … from the cache` |
| Slow load | A real request longer than the slow load threshold (default 1 s) |
| Cause of a slow load | The most recent `Expiring` event for that account before the request, or "first request from client" when the request had not been seen before. When that event is more than 15 minutes older than the request, the cause is "not rebuilt in time": a pass could have rebuilt the rows and had not, because the account was outside its usual hours or a pass ran out of time. With no event at all, the cause is "expired, no event logged" |
| Hit rate without own playback | Cache hits divided by all loads except those rebuilt because of the account's own playback, which Plex discards again within seconds |
| Row build cost | `Computed … in N ms` |

Plex's own hit ratio line counts the warmer's requests as well and is therefore higher than the hit rate for real loads.

## Resource use

Measured on the reference server:

- Learning: one log download of about 6 MB (compressed) every 10 minutes.
- A replay of a cached request: a few milliseconds.
- A replay of an uncached page: the page's build time, between 1 and 5 seconds on this server.
- Session checks: one small request every 15 seconds.

Smart mode, the pass time budget and the transcode limit bound the work done in any period.
