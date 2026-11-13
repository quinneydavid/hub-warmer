# Security

## Secrets

| Secret | Location | Purpose |
|---|---|---|
| Owner token | `DATA_DIR/config.json` (mode 600), or `PLEX_TOKEN` | Reads the Plex log (`/diagnostics/logs`), accounts and sessions, and obtains server access tokens for shared accounts from plex.tv |
| Server access tokens for shared accounts | Memory only, refreshed every 15 minutes | Used to request each account's rows as that account. Never written to disk, logged or displayed |
| Session secret | `DATA_DIR/config.json` | Signs session cookies for the web interface |

## Web interface

- Sign-in uses the plex.tv PIN flow. Only the server owner and accounts the server is shared with are admitted. Shared accounts see overall figures and their own devices. Only the owner can change settings or start a pass.
- First run: until an owner has signed in (or `PLEX_TOKEN` is set), the page accepts the Plex Media Server address without a sign-in. Whoever can reach the page in that window can set it. Before sign-in the address is only used for a `/identity` request and must answer as a Plex Media Server. The owner sign-in that follows is checked against plex.tv for the server that answered, and from then on the owner's Plex token is sent to that address with every request. A device on the same network could therefore enter its own address and pass on the real server's identity to receive the token. To prevent that, the sign-in step shows the stored address, and a sign-in is refused if the address changed after it began: check the address before choosing Sign in with Plex. Complete setup on a network you trust, or set `PLEX_URL` so the form is never offered. Afterwards only the owner can change the address.
- A sign-in can only be completed by the browser that started it (a one-time cookie is tied to the plex.tv PIN), and at most 50 sign-ins can be under way at once.
- Session cookies are signed (HMAC-SHA256), `HttpOnly`, `SameSite=Lax`, and `Secure` behind HTTPS. Every state-changing request requires a custom header that only the page's own scripts send.
- Tokens are never displayed.
- Run the warmer on a local network or behind a reverse proxy with HTTPS. Do not expose port 8080 to the internet directly.

## plex.tv calls with a server token

When the warmer is given the server's own token (for example `PlexOnlineToken` from `Preferences.xml`), plex.tv attributes requests made with that token to the server device, including the version they report. For every plex.tv call made with such a token, the warmer therefore identifies itself with the server's client id, product name and version, so the server's registered version is not altered. Tokens obtained through Sign in with Plex belong to the warmer's own device and are used as such.

## Reporting a vulnerability

Report vulnerabilities through a private security advisory on GitHub (the repository's Security tab → Report a vulnerability). Please do not open a public issue with the details.
