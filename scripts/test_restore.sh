#!/usr/bin/env bash
# End-to-end restore test.
#
# Spins up an isolated Docker Compose stack, seeds test data, takes a full
# pgBackRest backup, destroys the postgres data directory, restores from the
# backup, and verifies that document counts match.
#
# Runs automatically every week via .github/workflows/restore-test.yml.
# Can also be run manually: make test-restore
#
# Exit 0 = restore verified.
# Exit 1 = test failed (see output for details).
set -euo pipefail

TIMESTAMP=$(date +%s)
PROJECT="restore-test-${TIMESTAMP}"
DC="docker compose -p ${PROJECT}"

# Source .env so POSTGRES_USER etc. are available
if [[ -f .env ]]; then
  set -o allexport; source .env; set +o allexport
fi

PG_USER="${POSTGRES_USER:-pdfextract}"
PG_DB="${POSTGRES_DB:-pdfextract_dev}"
PG_PASS="${POSTGRES_PASSWORD:-changeme}"

log()  { echo "[$(date -u '+%H:%M:%S')] $*"; }
step() { echo ""; log "==> [${1}] ${2}"; }

cleanup() {
  log "Cleaning up project '${PROJECT}'..."
  $DC down -v --remove-orphans 2>/dev/null || true
}
trap cleanup EXIT INT TERM

step "1/9" "Building images..."
$DC build postgres api 2>&1 | tail -5

step "2/9" "Starting postgres (isolated stack)..."
$DC up -d postgres

log "Waiting for postgres to be healthy..."
for i in $(seq 1 90); do
  if $DC exec -T postgres pg_isready -U "${PG_USER}" -q 2>/dev/null; then
    break
  fi
  if [[ $i -eq 90 ]]; then
    log "ERROR: postgres did not become healthy in time."
    exit 1
  fi
  sleep 2
done
log "Postgres healthy."

step "3/9" "Starting pgbackrest-cron (creates stanza if needed)..."
$DC up -d pgbackrest-cron
# Give it time to connect and create the stanza
sleep 10

step "4/9" "Running database migrations..."
$DC run --rm --no-deps \
  -e POSTGRES_HOST=postgres \
  -e POSTGRES_USER="${PG_USER}" \
  -e "POSTGRES_PASSWORD=${PG_PASS}" \
  -e POSTGRES_DB="${PG_DB}" \
  -e POSTGRES_PORT=5432 \
  api alembic upgrade head

step "5/9" "Seeding known test documents..."
$DC exec -T postgres psql -U "${PG_USER}" -d "${PG_DB}" <<SQL
INSERT INTO document (filename, sha256, status, created_at)
VALUES
  ('restore-smoke-1.pdf', 'a1b2c3d4e5f601020304050607080901', 'verified',     NOW()),
  ('restore-smoke-2.pdf', 'a1b2c3d4e5f601020304050607080902', 'approved',     NOW()),
  ('restore-smoke-3.pdf', 'a1b2c3d4e5f601020304050607080903', 'needs_review', NOW()),
  ('restore-smoke-4.pdf', 'a1b2c3d4e5f601020304050607080904', 'failed',       NOW()),
  ('restore-smoke-5.pdf', 'a1b2c3d4e5f601020304050607080905', 'uploaded',     NOW())
ON CONFLICT DO NOTHING;
SQL

COUNT_BEFORE=$($DC exec -T postgres psql -U "${PG_USER}" -d "${PG_DB}" \
  -tAc "SELECT COUNT(*) FROM document WHERE filename LIKE 'restore-smoke-%'" \
  | tr -d '[:space:]')
log "Documents seeded: ${COUNT_BEFORE}"

step "6/9" "Triggering manual full backup..."
$DC exec -T pgbackrest-cron pgbackrest backup --stanza=main --type=full
log "Backup complete."
$DC exec -T pgbackrest-cron pgbackrest info --stanza=main

step "7/9" "Stopping postgres and destroying data directory..."
$DC stop postgres pgbackrest-cron

# Build the postgres image tag to reference it in docker run
POSTGRES_IMAGE=$($DC images -q postgres | head -1)
if [[ -z "${POSTGRES_IMAGE}" ]]; then
  log "ERROR: could not determine postgres image ID."
  exit 1
fi

log "Clearing pgdata and restoring from backup..."
docker run --rm \
  --user root \
  -v "${PROJECT}_pgdata:/var/lib/postgresql/data" \
  -v "${PROJECT}_pgbackups:/var/lib/pgbackrest" \
  -v "${PROJECT}_pglogs:/var/log/pgbackrest" \
  "${POSTGRES_IMAGE}" \
  bash -c "
    chown -R postgres:postgres /var/lib/pgbackrest /var/log/pgbackrest
    rm -rf /var/lib/postgresql/data/*
    gosu postgres pgbackrest restore --stanza=main
  "

step "8/9" "Restarting postgres with restored data..."
$DC up -d postgres

log "Waiting for postgres to become healthy after restore..."
for i in $(seq 1 90); do
  if $DC exec -T postgres pg_isready -U "${PG_USER}" -q 2>/dev/null; then
    break
  fi
  if [[ $i -eq 90 ]]; then
    log "ERROR: postgres did not recover in time."
    exit 1
  fi
  sleep 2
done
log "Postgres healthy after restore."

step "9/9" "Verifying restored data..."
COUNT_AFTER=$($DC exec -T postgres psql -U "${PG_USER}" -d "${PG_DB}" \
  -tAc "SELECT COUNT(*) FROM document WHERE filename LIKE 'restore-smoke-%'" \
  | tr -d '[:space:]')
log "Documents after restore: ${COUNT_AFTER}"

if [[ "${COUNT_BEFORE}" != "${COUNT_AFTER}" ]]; then
  log "FAIL: document count mismatch — before=${COUNT_BEFORE}, after=${COUNT_AFTER}"
  exit 1
fi

# Full verification suite
COMPOSE_PROJECT="${PROJECT}" \
POSTGRES_USER="${PG_USER}" \
POSTGRES_DB="${PG_DB}" \
  scripts/restore_verify.sh \
    --compose-project "${PROJECT}" \
    --expected-docs "${COUNT_AFTER}"

log ""
log "======================================================"
log "  RESTORE TEST PASSED in project '${PROJECT}'"
log "  Seeded ${COUNT_BEFORE} docs → backed up → restored → verified ${COUNT_AFTER} docs"
log "======================================================"
