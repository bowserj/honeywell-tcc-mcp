#!/bin/sh
# Ensure /data is writable before starting the server.
# Works when the container runs as root (named volume / bind mount created
# by an older image) and no-ops when it runs as the mcp user.
if [ -d /data ] && [ -w /data ] 2>/dev/null; then
    :
elif [ "$(id -u)" = "0" ]; then
    chown -R 10001:10001 /data 2>/dev/null || true
fi
mkdir -p /data/exports
exec "$@"
