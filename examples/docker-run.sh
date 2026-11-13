#!/bin/sh
# Hub Warmer with docker run. After starting, open http://<host>:8080: enter the Plex address, then sign in with the
# account that owns the Plex server. All other settings are on the Settings page.
docker run -d --name hub-warmer --restart unless-stopped \
  -p 8080:8080 \
  -e TZ=Europe/London \
  -v hub-warmer:/data \
  ghcr.io/quinneydavid/hub-warmer:latest

# On the Plex host, to read the Logs folder directly instead of downloading logs through the API: mount it, then set
# Settings > Log source to /plexlogs.
#   docker run -d --name hub-warmer --restart unless-stopped --network host -e TZ=Europe/London \
#     -v "/var/lib/plexmediaserver/Library/Application Support/Plex Media Server/Logs:/plexlogs:ro" \
#     -v hub-warmer:/data ghcr.io/quinneydavid/hub-warmer:latest
