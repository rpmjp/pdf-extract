#!/usr/bin/env bash
# Restore PostgreSQL from the most recent pgBackRest backup.
#
# Usage:
#   scripts/restore_postgres.sh [--compose-project NAME] [--target TIMESTAMP]
#
# Options:
#   --compose-project NAME   Docker Compose project name (default: pdf-extract)
#   --target TIMESTAMP       Point-in-time target, e.g. "2024-01-15 02:30:00"
#                            Omit to restore to the most recent backup.
#   --dry-run                Print what would happen without doing it.
#
# Prerequisites:
#   - Docker and Docker Compose installed
#   - The pgbackups volume contains at least one full backup
#   - The POSTGRES_USER and POSTGRES_PASSWORD env vars are set (or in .env)
#
# RTO target: < 1 hour for a typical database.
# RPO target: < 1 hour (hourly WAL archival, archive_timeout=60s).
set -euo pipefail

COMPOSE_PROJECT="${COMPOSE_PROJECT:-pdf-extract}"
STANZA="main"
DRY_RUN=false
PIT_TARGET=""

while [[ $# -gt 0 ]]; do
  case $1 in
    --compose-project) COMPOSE_PROJECT="$2"; shift 2 ;;
    --target)          PIT_TARGET="$2"; shift 2 ;;
    --dry-run)         DRY_RUN=true; shift ;;
    *) echo "Unknown argument: $1"; exit 1 ;;
  esac
done

DC="docker compose -p ${COMPOSE_PROJECT}"

log() { echo "[$(date -u '+%H:%M:%S')] $*"; }
dryrun() {
  if $DRY_RUN; then
    echo "[DRY-RUN] $*"
  else
    eval "$*"
  fi
}

log "=== PostgreSQL restore from pgBackRest backup ==="
log "Compose project : ${COMPOSE_PROJECT}"
log "Stanza          : ${STANZA}"
log "Point-in-time   : ${PIT_TARGET:-<most recent>}"

# ── Step 1: Confirm the backup exists ─────────────────────────────────────────
log ""
log "[1/5] Verifying backup repository..."
$DC exec -T pgbackrest-cron pgbackrest info --stanza="${STANZA}" \
  || { log "ERROR: No backup found in repository. Has pgbackrest-cron run yet?"; exit 1; }

# ── Step 2: Stop the API and worker so no writes happen during restore ─────────
log ""
log "[2/5] Stopping api, worker, and postgres (preserving data volumes)..."
dryrun "$DC stop api worker pgbackrest-cron postgres"

# ── Step 3: Clear the postgres data directory and run restore ─────────────────
log ""
log "[3/5] Clearing pgdata and restoring from backup..."

RESTORE_CMD="pgbackrest restore --stanza=${STANZA} --delta"
if [[ -n "${PIT_TARGET}" ]]; then
  RESTORE_CMD="${RESTORE_CMD} --type=time --target='${PIT_TARGET}' --target-action=promote"
fi

# Run the restore inside a temporary container that has access to both
# the pgdata and pgbackups volumes.
dryrun "docker run --rm \
  --user root \
  -v ${COMPOSE_PROJECT}_pgdata:/var/lib/postgresql/data \
  -v ${COMPOSE_PROJECT}_pgbackups:/var/lib/pgbackrest \
  -v ${COMPOSE_PROJECT}_pglogs:/var/log/pgbackrest \
  \$(${DC} images -q postgres | head -1) \
  bash -c 'chown -R postgres:postgres /var/lib/pgbackrest /var/log/pgbackrest && \
           gosu postgres ${RESTORE_CMD}'"

# ── Step 4: Restart postgres ───────────────────────────────────────────────────
log ""
log "[4/5] Starting postgres with restored data..."
dryrun "$DC up -d postgres"
log "Waiting for postgres to become healthy..."
if ! $DRY_RUN; then
  for i in $(seq 1 60); do
    if $DC exec -T postgres pg_isready -U "${POSTGRES_USER:-pdfextract}" -q 2>/dev/null; then
      break
    fi
    sleep 3
  done
fi

# ── Step 5: Restart application services ──────────────────────────────────────
log ""
log "[5/5] Restarting api, worker, and pgbackrest-cron..."
dryrun "$DC up -d api worker pgbackrest-cron"

log ""
log "=== Restore complete. Run scripts/restore_verify.sh to validate. ==="
