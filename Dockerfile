# Honeywell Total Connect Comfort MCP server
#
#   docker build -t honeywell-tcc-mcp .
#   docker run --rm -i --env TCC_USERNAME --env TCC_PASSWORD honeywell-tcc-mcp
#
# Session persistence: mount a volume (or bind dir) at /data and the cached
# session cookie survives container restarts:
#   docker run -v tcc-data:/data ... honeywell-tcc-mcp
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TCC_SESSION_FILE=/data/tcc_session.json \
    TCC_CREDENTIALS_FILE=/data/credentials.json \
    TCC_EXPORTS_DIR=/data/exports

# /data holds the cached session + optional credentials file + report exports
# (not in image); ownership must be the mcp user so the container can write
# to /data and create files under /data/exports on a fresh volume
RUN useradd -m -u 10001 mcp && mkdir -p /data/exports && chown -R mcp /data
USER mcp

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chmod=755 server.py tcc_api_client.py entrypoint.sh ./

# MCP server speaks on stdin/stdout
# --chmod=755: COPY preserves the build-context file mode, and the
# entrypoint is executed by PATH through sh -c, so the exec bit must be
# set here (a RUN chmod after USER mcp cannot chmod the root-owned file).
ENTRYPOINT ["/bin/sh", "-c", "/app/entrypoint.sh \"$@\"", "--"]
CMD ["python", "server.py"]
