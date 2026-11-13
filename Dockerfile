FROM python:3.12-alpine
LABEL org.opencontainers.image.title="hub-warmer" \
      org.opencontainers.image.description="Keeps Plex Home and library rows pre-built for every user and app" \
      org.opencontainers.image.source="https://github.com/quinneydavid/hub-warmer" \
      org.opencontainers.image.licenses="MIT"
COPY hubwarmer /app/hubwarmer
COPY LICENSE /app/LICENSE
# A named volume mounted on /data takes its owner from this folder: the app runs as user 1000 and must be able to write it.
RUN mkdir -p /data && chown 1000:1000 /data
WORKDIR /app
USER 1000:1000
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 DATA_DIR=/data
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=5s CMD wget -qO- "http://127.0.0.1:${WEB_PORT:-8080}/healthz" || exit 1
ENTRYPOINT ["python3", "-m", "hubwarmer"]
