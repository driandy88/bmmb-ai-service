#!/usr/bin/env bash
# Starts a local Cloud SQL Auth Proxy for the extraction service's Cloud SQL
# instance, so config.py's DB_HOST fallback (see its module docstring) can
# be used instead of the Cloud SQL Python Connector -- no `gcloud auth
# application-default login` required, just the proxy binary's own
# credentials (`gcloud auth login`, checked once at startup).
#
# Usage:
#   services/extraction/scripts/db_proxy.sh [port]   # default port 5433
#
# Then point the app at it:
#   DB_HOST=127.0.0.1 DB_PORT=5433 DB_USER=... DB_PASS=... APP_ENV=dev \
#     uvicorn services.extraction.api:app
#
# For `docker run` instead of a bare uvicorn process, the container can't
# reach the host via 127.0.0.1 -- use DB_HOST=host.docker.internal (works
# out of the box on Docker Desktop for Mac/Windows; on Linux add
# `--add-host=host.docker.internal:host-gateway` to `docker run`).
set -euo pipefail

PORT="${1:-5433}"
REPO_ROOT="$(git rev-parse --show-toplevel)"

if [ -z "${INSTANCE_CONNECTION_NAME:-}" ] && [ -f "$REPO_ROOT/.env" ]; then
  INSTANCE_CONNECTION_NAME="$(grep -E '^INSTANCE_CONNECTION_NAME=' "$REPO_ROOT/.env" | cut -d= -f2-)"
fi

if [ -z "${INSTANCE_CONNECTION_NAME:-}" ]; then
  echo "INSTANCE_CONNECTION_NAME not set and not found in $REPO_ROOT/.env" >&2
  echo "Find it with: gcloud sql instances describe INSTANCE_NAME --format='value(connectionName)'" >&2
  exit 1
fi

if ! command -v cloud-sql-proxy >/dev/null 2>&1; then
  echo "cloud-sql-proxy not found. Install with: brew install cloud-sql-proxy" >&2
  echo "(see https://cloud.google.com/sql/docs/postgres/sql-proxy for other platforms)" >&2
  exit 1
fi

echo "Starting Cloud SQL Auth Proxy for $INSTANCE_CONNECTION_NAME on 127.0.0.1:$PORT"
echo "Run the service against it with: DB_HOST=127.0.0.1 DB_PORT=$PORT"
exec cloud-sql-proxy --port "$PORT" "$INSTANCE_CONNECTION_NAME"
