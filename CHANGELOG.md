# Changelog

## 0.6.1 (2026-11-13)
First version published on GitHub.
- Fixed: the container could not start with a new Docker volume (`/data` belonged to root while the app runs as user 1000). The image now owns `/data`; the examples use a named volume and say what a mounted folder needs.
- Sign-in: a sign-in can only be finished by the browser that started it, and during setup it is refused if the server address was changed meanwhile. The setup sign-in step shows the address the Plex token will be sent to. At most 50 sign-ins can be under way at once.
- The sign-in lifetime set on the Settings page now also sets how long the session itself is valid, not only the cookie.
- Images are published on GHCR only: `ghcr.io/quinneydavid/hub-warmer`. The health check follows `WEB_PORT`. The image contains the licence text.
- Screenshots in the README, made from a demo with invented data. `tools/demo/` runs Hub Warmer against a fake Plex server and regenerates them; it is not part of the image.
- Page: the "installed" marker no longer overlaps the load count above its bar; wide tables scroll inside their card on a phone instead of widening the page; the address fields on the Settings page are wide enough to read.
- Documentation: `PLEX_PREFERENCES` is listed, the metric name `hub_warmer_plex_hubcache_hit_ratio` and the local run command are corrected.

## 0.6.0 (2026-11-12)
- Prepared for publication: README, examples, tests, multi-arch image build. MIT licence.
- Renamed to Hub Warmer. The Python package is `hubwarmer`, the image is `ghcr.io/quinneydavid/hub-warmer`, the metric prefix is `hub_warmer_` and the Grafana dashboard is `grafana/hub-warmer.json`. The device name shown on plex.tv is "Hub Warmer". Files in `/data` are unchanged; the session cookie has a new name, so one new sign-in is needed.
- Setup on the page. With no address configured, the page asks for the Plex Media Server address, tests it and stores it, then offers Sign in with Plex. No environment variable is required. The address form is open only until an owner has signed in.
- The Plex address, log source, public address, own addresses, history length, sign-in lifetime and metrics access moved to the Settings page. Their environment variables remain as optional defaults. Only `DATA_DIR`, `WEB_PORT`, `WEB_BIND`, `WEB_AUTH` and `PLEX_TOKEN` are environment-only.
- `PLEX_URL` no longer defaults to `http://127.0.0.1:32400`; an install that relied on the default enters the address once on the page.
- `INCLUDE_USERS` and `EXCLUDE_USERS` still work but are no longer documented; the per-account switches on the Accounts page replace them.

## 0.5.1 (2026-11-11)
- Poster statistics on the Overview page: real apps' poster requests per day split into instant, 30-80 ms and prepared on request, with the daily share answered at once and the average time; the warmer's own checked and prepared counts and time per day; queue length and the number of request forms learned. Metrics `user_poster_requests_total` and `user_poster_instant_total`.

## 0.5.0 (2026-11-11)
- Posters. Plex resizes every poster for the device that asks and only then keeps the result; on the reference server 72% of real poster requests were not in that cache and took 135 ms each at the median. The warmer now learns from the Plex log how each account's apps ask for posters (size parameters, relative path or absolute URL with the user's token) and requests the posters of every warmed screen in advance, within its own time budget. Apps that pass an absolute URL with a token get a per-user cache entry in Plex, so those are prepared per account. Settings `WARM_POSTERS` and `POSTER_BUDGET_SECONDS`; metrics `poster_requests_total`, `posters_built_total`, `posters_queued`.

## 0.4.3 (2026-11-11)
- Devices are identified from Plex's own per-device traffic statistics (`/statistics/bandwidth`), not only by address. Two devices behind one address (a household watching remotely) are told apart, and every device shows its real name without having to play something first. The address is still used when the statistics cannot name the device.
- While an account is playing, requests that only cover the library being watched are not replayed on any of its devices: Plex drops those rows again within seconds. Its other libraries and the combined Home screen are still kept warm.
- A paused stream no longer counts as playing. A paused device is warmed like any other, and pausing triggers the same immediate warm as stopping.
- Playback is tracked per device. While an account plays, only the requests learned from the playing device are skipped; the account's other devices are still kept warm. Previously the whole account was skipped, so anyone browsing on a second device, or on a shared account, got uncached pages for as long as something was playing.
- Slow loads from an account that has been watching are attributed to "Account was playing" even when the cache was dropped more than 15 minutes earlier.

## 0.4.2 (2026-11-11)
- Home first: in smart mode the Home screen requests of every account are replayed on every pass (`HOME_ALWAYS`, default on), so Home is cached whenever someone opens Plex. Library pages still follow the smart schedule, and accounts with an active playback session are still skipped.

## 0.4.1 (2026-11-10)
- Documentation rewritten.
- Slow loads are now defined by a threshold (`SLOW_LOAD_SECONDS`, default 1 s, editable under Settings). Only loads over it are listed and attributed to a cause; a rebuild that finished in a few milliseconds is no longer reported as a wait.
- New cause "Cached, still slow" for slow loads whose rows all came from the cache.
- Interface text rewritten: consistent names for tiles, charts, causes, plan states and settings.
- Metric `user_row_rebuilds_total{cause}` is replaced by `user_slow_loads_total{cause}`.

## 0.4.0 (2026-11-10)
Measurements from a day of use showed 58 replayed requests per real page load, 43% of rebuilds for accounts that were not in use, and most remaining uncached loads following the account's own playback.
- Playback-aware: accounts that are watching are skipped (Plex drops their rows every few seconds) and warmed as soon as playback ends. Sessions are checked every 15 s.
- Smart mode is now the default; apps are forgotten after 3 unused days (was 14) and each account keeps its 12 most recently used requests (was 30).
- Time budget per pass (90 s), most urgent accounts first; the rest go first next time.
- The probe is skipped while the owner is watching (it would rebuild the owner's rows on every check).
- Real users' cache hit rate, measured from the Plex log, replaces Plex's own hit ratio on the page (that one also counts the warmer's requests). Every real load that waited is listed with its cause.
- "What a cold load costs": build time of every row, from the Plex log.
- After playback the device the account was watching on is warmed first.
- Charts show the whole picture instead of only "under 1 s": every real load by speed band per day and per hour, loads served from cache or rebuilt (by cause) per day, a per-screen table, the latest slow loads with the rows Plex had to rebuild for each, time waited per cause, and what the warmer did hour by hour.
- Metrics: `user_row_cache_requests_total`, `user_row_cache_hits_total`, `user_row_rebuilds_total{cause}`, `accounts_watching`.

## 0.3.0 (2026-11-09)
- Settings page: next check / top-up / log read, what the next pass will warm and why, every setting editable at runtime.
- Devices: requests grouped per device, named from Plex's own player info; warming on/off per account and per device.
- Smart mode: warm each account around its usual hours (learned per hour of the week) or when recently active.
- Charts: share of row loads under 1 s per day with a "warmer on" marker, row building done in advance per day, per-account and per-device charts, usual-hours heatmap.
- Detects its own address automatically (a marked request it finds in the Plex log), so it never learns its own replays, wherever it runs.
- Log parser: IPv6 addresses; `Completed` lines without extra fields were missed.

## 0.2.1 (2026-11-09)
- plex.tv calls made with a server token now identify as the server itself. Earlier builds could make plex.tv list the server under the warmer's version number, which newer Plex apps reject as outdated.

## 0.2.0 (2026-11-09)
- Web page with Sign in with Plex (owner / friend roles; first owner sign-in completes setup).
- Prometheus `/metrics` (including real users' row load times), `/api/status`, Grafana dashboard.

## 0.1.0 (2026-11-09)
- Learn each app's hub requests from Plex's HubCache log lines (including the app's language and feature headers) and replay them per account when the cache was dropped, plus a regular top-up.
- Logs from the Plex API (`/diagnostics/logs`) or a mounted folder.
