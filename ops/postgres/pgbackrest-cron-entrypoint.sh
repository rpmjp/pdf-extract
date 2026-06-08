#!/bin/bash
# Entrypoint for the pgbackrest-cron sidecar service.
# This container shares the pgdata volume (read-only) and pgbackups volume
# with the postgres container, and connects to postgres via TCP for
# checkpoint control (pg_backup_start / pg_backup_stop).
set -euo pipefail

STANZA="${PGBACKREST_STANZA:-main}"

echo "==> pgbackrest-cron: waiting for postgres to accept connections..."
until pg_isready -h "${PGHOST:-postgres}" -p "${PGPORT:-5432}" -U "${PGUSER:-postgres}" -q; do
  sleep 2
done
echo "==> pgbackrest-cron: postgres is ready."

# Ensure the stanza exists (idempotent — safe to run on every restart)
if ! pgbackrest info --stanza="${STANZA}" 2>/dev/null | grep -q "status:"; then
  echo "==> pgbackrest-cron: creating stanza '${STANZA}'..."
  pgbackrest stanza-create --stanza="${STANZA}"
else
  echo "==> pgbackrest-cron: stanza '${STANZA}' already exists."
fi

echo "==> pgbackrest-cron: starting backup scheduler (supercronic)."
exec supercronic /etc/backup-schedule
