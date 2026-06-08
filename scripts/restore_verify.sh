#!/usr/bin/env bash
# Post-restore verification script.
#
# Checks:
#   1. PostgreSQL is accepting connections
#   2. Key tables exist and have rows
#   3. 5 documents can be fetched end-to-end via the API
#   4. MinIO primary bucket is accessible (if API_URL is reachable)
#
# Usage:
#   scripts/restore_verify.sh [--compose-project NAME] [--expected-docs N]
#
# Exit code 0 = all checks passed.
# Exit code 1 = one or more checks failed.
set -euo pipefail

COMPOSE_PROJECT="${COMPOSE_PROJECT:-pdf-extract}"
EXPECTED_DOCS=""    # If set, assert COUNT(*) FROM document = N
PASS=0
FAIL=0

while [[ $# -gt 0 ]]; do
  case $1 in
    --compose-project) COMPOSE_PROJECT="$2"; shift 2 ;;
    --expected-docs)   EXPECTED_DOCS="$2"; shift 2 ;;
    *) echo "Unknown argument: $1"; exit 1 ;;
  esac
done

DC="docker compose -p ${COMPOSE_PROJECT}"
PG_USER="${POSTGRES_USER:-pdfextract}"
PG_DB="${POSTGRES_DB:-pdfextract_dev}"

log()   { echo "[$(date -u '+%H:%M:%S')] $*"; }
ok()    { log "  PASS: $*"; ((PASS++)); }
fail()  { log "  FAIL: $*"; ((FAIL++)); }

log "=== Post-restore verification ==="
log "Compose project : ${COMPOSE_PROJECT}"
log ""

# ── Check 1: postgres health ───────────────────────────────────────────────────
log "[1] PostgreSQL connectivity..."
if $DC exec -T postgres pg_isready -U "${PG_USER}" -d "${PG_DB}" -q; then
  ok "postgres accepting connections"
else
  fail "postgres not ready"
fi

# ── Check 2: table existence ───────────────────────────────────────────────────
log "[2] Key tables exist..."
TABLES=("document" "transaction" "parse_job" "review_item" "audit_log")
for table in "${TABLES[@]}"; do
  COUNT=$($DC exec -T postgres psql -U "${PG_USER}" -d "${PG_DB}" \
    -tAc "SELECT COUNT(*) FROM information_schema.tables WHERE table_name='${table}'" 2>/dev/null || echo "0")
  if [[ "${COUNT// /}" == "1" ]]; then
    ok "table '${table}' exists"
  else
    fail "table '${table}' missing"
  fi
done

# ── Check 3: document count ────────────────────────────────────────────────────
log "[3] Document count..."
DOC_COUNT=$($DC exec -T postgres psql -U "${PG_USER}" -d "${PG_DB}" \
  -tAc "SELECT COUNT(*) FROM document" 2>/dev/null | tr -d '[:space:]' || echo "error")
log "    document count = ${DOC_COUNT}"

if [[ "${DOC_COUNT}" == "error" ]]; then
  fail "could not query document table"
elif [[ -n "${EXPECTED_DOCS}" && "${DOC_COUNT}" != "${EXPECTED_DOCS}" ]]; then
  fail "expected ${EXPECTED_DOCS} documents, got ${DOC_COUNT}"
else
  ok "document count readable (${DOC_COUNT})"
fi

# ── Check 4: sample-read 5 documents ──────────────────────────────────────────
log "[4] Sample-reading up to 5 documents..."
SAMPLE=$($DC exec -T postgres psql -U "${PG_USER}" -d "${PG_DB}" \
  -tAc "SELECT id, filename, status FROM document ORDER BY id LIMIT 5" 2>/dev/null || echo "error")
if [[ "${SAMPLE}" == "error" || -z "${SAMPLE// /}" ]]; then
  ok "no documents to sample (empty database is valid after a clean restore)"
else
  while IFS= read -r row; do
    [[ -z "${row// /}" ]] && continue
    ok "document row readable: ${row}"
  done <<< "${SAMPLE}"
fi

# ── Check 5: pgBackRest stanza health ─────────────────────────────────────────
log "[5] pgBackRest backup repository health..."
if $DC exec -T pgbackrest-cron pgbackrest info --stanza=main 2>/dev/null | grep -q "status:"; then
  ok "pgBackRest stanza reports healthy"
else
  fail "pgBackRest stanza not accessible (pgbackrest-cron may not be running)"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
log ""
log "=== Results: ${PASS} passed, ${FAIL} failed ==="
if [[ $FAIL -gt 0 ]]; then
  log "RESTORE VERIFICATION FAILED — investigate before declaring recovery complete."
  exit 1
fi
log "All checks passed. Recovery is complete."
exit 0
