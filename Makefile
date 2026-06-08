.PHONY: help up down build logs backup backup-verify test-restore restore-postgres restore-minio

# Default target
help:
	@echo "pdf-extract operations targets"
	@echo ""
	@echo "  up               Start all services (dev stack)"
	@echo "  down             Stop and remove containers (volumes preserved)"
	@echo "  build            Rebuild all images"
	@echo "  logs             Follow logs for all services"
	@echo ""
	@echo "  backup           Trigger an immediate pgBackRest full backup"
	@echo "  backup-verify    Run pgBackRest integrity check on the repository"
	@echo "  test-restore     Full isolated restore test (spins up a fresh stack,"
	@echo "                   seeds data, backs up, restores, verifies, tears down)"
	@echo "  restore-postgres Run the postgres restore runbook interactively"
	@echo "  restore-minio    Run the MinIO restore runbook interactively"
	@echo ""
	@echo "Environment: set COMPOSE_PROJECT to target a non-default stack."

# ── Dev lifecycle ──────────────────────────────────────────────────────────────

up:
	docker compose up -d

down:
	docker compose down

build:
	docker compose build

logs:
	docker compose logs -f

# ── Backup operations ──────────────────────────────────────────────────────────

# Trigger an immediate full backup without waiting for the 02:00 schedule.
backup:
	docker compose exec pgbackrest-cron \
	  pgbackrest backup --stanza=main --type=full

# Verify backup integrity (WAL archive chain + file checksums).
# Run this daily to confirm backups are restorable without actually restoring.
backup-verify:
	docker compose exec pgbackrest-cron \
	  pgbackrest check --stanza=main
	docker compose exec pgbackrest-cron \
	  pgbackrest info --stanza=main

# ── Restore operations ─────────────────────────────────────────────────────────

# Full end-to-end restore test in an isolated stack.
# Spins up a clean environment, seeds documents, takes a backup, destroys
# postgres data, restores, and verifies counts match.  Exits 0 on success.
# Typical runtime: 3-5 minutes.
test-restore:
	@echo "Starting restore test (isolated stack — will not affect your dev data)..."
	bash scripts/test_restore.sh

# Run the postgres restore runbook against the running dev stack.
restore-postgres:
	bash scripts/restore_postgres.sh

# Run the MinIO restore runbook against the running dev stack.
restore-minio:
	bash scripts/restore_minio.sh
