#!/usr/bin/env bash
# Restore MinIO objects from the replica bucket to the primary bucket.
#
# In normal operation, minio-sync mirrors primary → replica continuously.
# If the primary MinIO volume is lost, this script reverses the flow:
# replica → primary (new empty volume).
#
# Usage:
#   scripts/restore_minio.sh [--compose-project NAME] [--dry-run]
#
# Prerequisites:
#   - Docker Compose running with minio and minio-replica services
#   - MINIO_ROOT_USER and MINIO_ROOT_PASSWORD env vars are set (or in .env)
set -euo pipefail

COMPOSE_PROJECT="${COMPOSE_PROJECT:-pdf-extract}"
DRY_RUN=false

while [[ $# -gt 0 ]]; do
  case $1 in
    --compose-project) COMPOSE_PROJECT="$2"; shift 2 ;;
    --dry-run)         DRY_RUN=true; shift ;;
    *) echo "Unknown argument: $1"; exit 1 ;;
  esac
done

DC="docker compose -p ${COMPOSE_PROJECT}"
BUCKET="${MINIO_BUCKET:-documents}"

log() { echo "[$(date -u '+%H:%M:%S')] $*"; }
dryrun() {
  if $DRY_RUN; then
    echo "[DRY-RUN] $*"
  else
    eval "$*"
  fi
}

log "=== MinIO restore: replica → primary ==="
log "Compose project : ${COMPOSE_PROJECT}"
log "Source bucket   : ${BUCKET}-backup  (replica)"
log "Target bucket   : ${BUCKET}         (primary)"

# ── Step 1: Verify replica is accessible ──────────────────────────────────────
log ""
log "[1/4] Verifying replica bucket..."
$DC exec -T minio-replica \
  mc alias set replica-local http://localhost:9000 \
    "${MINIO_ROOT_USER}" "${MINIO_ROOT_PASSWORD}" \
  && $DC exec -T minio-replica mc ls "replica-local/${BUCKET}-backup/" \
  || { log "ERROR: Replica bucket not accessible."; exit 1; }

# ── Step 2: Stop the API so no writes happen during restore ───────────────────
log ""
log "[2/4] Stopping api and worker..."
dryrun "$DC stop api worker"

# ── Step 3: Mirror from replica to primary ────────────────────────────────────
log ""
log "[3/4] Copying objects from replica to primary..."
dryrun "$DC exec -T minio-replica sh -c \"
  mc alias set primary-remote http://minio:9000 ${MINIO_ROOT_USER} ${MINIO_ROOT_PASSWORD} &&
  mc alias set local-replica  http://localhost:9000 ${MINIO_ROOT_USER} ${MINIO_ROOT_PASSWORD} &&
  mc mb --ignore-existing primary-remote/${BUCKET} &&
  mc mirror local-replica/${BUCKET}-backup primary-remote/${BUCKET}
\""

# ── Step 4: Restart the API ────────────────────────────────────────────────────
log ""
log "[4/4] Restarting api and worker..."
dryrun "$DC up -d api worker"

log ""
log "=== MinIO restore complete. ==="
log "Verify object counts manually:"
log "  docker compose -p ${COMPOSE_PROJECT} exec minio mc ls local/${BUCKET}/"
