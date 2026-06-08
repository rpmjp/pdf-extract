# Restore Runbook

**RTO target:** ≤ 1 hour from incident declaration to application serving traffic.  
**RPO target:** ≤ 1 hour of data loss (WAL archived every 60 seconds; daily full backup at 02:00 UTC).

This document is written for someone who has never seen the system before.
All commands run from the project root on the recovery host.

---

## Table of Contents

1. [Backup architecture](#1-backup-architecture)
2. [Before you start](#2-before-you-start)
3. [PostgreSQL restore](#3-postgresql-restore)
4. [MinIO restore](#4-minio-restore)
5. [Verify the restore](#5-verify-the-restore)
6. [Switching to production S3 backup](#6-switching-to-production-s3-backup)
7. [Backup verification (daily health check)](#7-backup-verification-daily-health-check)
8. [RTO / RPO reference](#8-rto--rpo-reference)

---

## 1. Backup architecture

### PostgreSQL (pgBackRest)

```
postgres container
 ├── WAL archiving → pgbackups volume   (continuous, every 60 s at most)
 └── /docker-entrypoint-initdb.d/       (creates stanza on first start)

pgbackrest-cron container
 ├── supercronic crontab:
 │    02:00 daily  → pgbackrest backup --type=full
 │    02:05 daily  → pgbackrest expire  (removes backups older than 7 days)
 │    03:00 Sunday → pgbackrest check   (verifies WAL chain integrity)
 └── shared volumes: pgdata (read-only), pgbackups (read-write)
```

The `pgbackups` Docker volume is the repository.  In production, swap
`repo1-type=posix` for `repo1-type=s3` (see §6).

### MinIO (object storage)

```
minio-sync container
 └── mc mirror --watch   primary/documents → replica/documents-backup
```

`minio-replica` runs on a separate Docker volume (`miniodata-replica`),
simulating a second availability zone.  If the primary MinIO volume is
lost, `scripts/restore_minio.sh` reverses the mirror direction.

---

## 2. Before you start

**Prerequisites on the recovery host:**

- Docker Engine ≥ 24 and Docker Compose plugin
- Git (to clone the repo and get scripts)
- Access to the backup volume — either:
  - The `pgbackups` Docker named volume (same host), OR
  - S3 credentials if using the cloud backend
- The `.env` file with database credentials (keep a secure copy offsite)

**Clone and configure:**

```bash
git clone <repo-url> pdf-extract
cd pdf-extract
cp .env.example .env
# Edit .env — set POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB,
#              MINIO_ROOT_USER, MINIO_ROOT_PASSWORD
```

**If restoring to a NEW host** (original host lost), transfer the backup
volume data first:

```bash
# On the backup host (if volumes are accessible):
docker run --rm -v pdf-extract_pgbackups:/src alpine \
  tar czf - /src | ssh recovery-host "docker run --rm -i \
    -v pdf-extract_pgbackups:/dst alpine tar xzf - -C /"
```

---

## 3. PostgreSQL restore

### Quick path (one command)

```bash
make restore-postgres
```

The script stops the application, runs `pgbackrest restore`, restarts
postgres, then restarts the API.  It prompts for confirmation before
destructive steps.

### Step-by-step (manual)

Use this if the script fails or you need point-in-time recovery.

#### 3.1 Verify a backup exists

```bash
docker compose exec pgbackrest-cron \
  pgbackrest info --stanza=main
```

Expected output includes `status: ok` and at least one full backup listed.
If this fails, see §3.5 (no backup found).

#### 3.2 Stop the application

```bash
docker compose stop api worker pgbackrest-cron postgres
```

#### 3.3 Clear the data directory and restore

```bash
# Get the postgres image ID
POSTGRES_IMAGE=$(docker compose images -q postgres | head -1)

docker run --rm \
  --user root \
  -v pdf-extract_pgdata:/var/lib/postgresql/data \
  -v pdf-extract_pgbackups:/var/lib/pgbackrest \
  -v pdf-extract_pglogs:/var/log/pgbackrest \
  "${POSTGRES_IMAGE}" \
  bash -c "
    chown -R postgres:postgres /var/lib/pgbackrest /var/log/pgbackrest
    rm -rf /var/lib/postgresql/data/*
    gosu postgres pgbackrest restore --stanza=main
  "
```

For **point-in-time recovery** (restore to a specific moment):

```bash
docker run --rm --user root \
  -v pdf-extract_pgdata:/var/lib/postgresql/data \
  -v pdf-extract_pgbackups:/var/lib/pgbackrest \
  "${POSTGRES_IMAGE}" \
  bash -c "
    gosu postgres pgbackrest restore --stanza=main \
      --type=time \
      --target='2024-01-15 03:45:00' \
      --target-action=promote
  "
```

Replace the timestamp with the recovery target (UTC).  Use the timestamp
just *before* the incident, not after.

#### 3.4 Start postgres and the application

```bash
docker compose up -d postgres

# Wait until healthy (usually < 30 s)
docker compose exec postgres pg_isready -U "${POSTGRES_USER}"

# Restart application services
docker compose up -d api worker pgbackrest-cron
```

#### 3.5 No backup found

If `pgbackrest info` shows no backups:

1. Check the `pgbackups` volume is mounted:
   ```bash
   docker volume inspect pdf-extract_pgbackups
   ```
2. Check pgbackrest-cron logs:
   ```bash
   docker compose logs pgbackrest-cron
   ```
3. Run an emergency backup of the *current* (possibly corrupted) database
   before proceeding, if postgres is still running:
   ```bash
   docker compose exec pgbackrest-cron \
     pgbackrest backup --stanza=main --type=full
   ```

---

## 4. MinIO restore

MinIO stores uploaded PDF files.  The replica bucket (`documents-backup`
on `minio-replica`) mirrors the primary continuously.

### Scenario A — Primary volume lost, replica intact

```bash
make restore-minio
```

Or manually:

```bash
# 1. Stop API writes
docker compose stop api worker

# 2. Start a fresh primary MinIO (new empty volume)
docker compose up -d minio

# 3. Mirror replica → primary
docker compose exec minio-replica sh -c "
  mc alias set primary http://minio:9000 \${MINIO_ROOT_USER} \${MINIO_ROOT_PASSWORD}
  mc alias set local   http://localhost:9000 \${MINIO_ROOT_USER} \${MINIO_ROOT_PASSWORD}
  mc mb --ignore-existing primary/documents
  mc mirror local/documents-backup primary/documents
"

# 4. Restart application
docker compose up -d api worker minio-sync
```

### Scenario B — Both volumes lost (no replica)

Object data cannot be recovered from database backups alone.  Documents
must be re-uploaded.  The database restore (§3) will restore metadata
(filename, status, extracted fields) but the PDF files will be absent.

**Checklist after database restore with no object recovery:**
- Set affected document statuses to `uploaded` (triggers re-parse on next upload)
- Notify users that their PDFs must be re-uploaded

---

## 5. Verify the restore

Run the verification script immediately after any restore:

```bash
scripts/restore_verify.sh
```

It checks:
- PostgreSQL connectivity
- All key tables exist (`document`, `transaction`, `parse_job`, `review_item`, `audit_log`)
- Document count is readable (sample 5 rows)
- pgBackRest stanza health

Expected output:
```
  PASS: postgres accepting connections
  PASS: table 'document' exists
  ...
  PASS: pgBackRest stanza reports healthy
=== Results: 7 passed, 0 failed ===
All checks passed. Recovery is complete.
```

If any check fails, investigate before declaring recovery complete and
resuming normal operations.

**Spot-check the application:**

```bash
# API health endpoint
curl http://localhost:${API_PORT}/health

# Check a specific document
curl -H "Authorization: Bearer <token>" \
  http://localhost:${API_PORT}/documents?per_page=5
```

---

## 6. Switching to production S3 backup

By default the backup repository is a local Docker volume (`posix` type).
For production, replace it with an S3-compatible backend.

### 6.1 Edit `ops/postgres/pgbackrest.conf`

```ini
[global]
repo1-type=s3
repo1-s3-bucket=your-backup-bucket
repo1-s3-endpoint=s3.amazonaws.com   # or compatible: Cloudflare R2, Backblaze B2
repo1-s3-region=us-east-1
repo1-s3-key=<AWS_ACCESS_KEY_ID>
repo1-s3-key-secret=<AWS_SECRET_ACCESS_KEY>
repo1-path=/pgbackrest/main

# Keep the same retention settings
repo1-retention-full=7
repo1-retention-archive=7
```

For **GCS** use `repo1-type=gcs` with `repo1-gcs-key=/path/to/service-account.json`.  
For **Azure** use `repo1-type=azure` with `repo1-azure-account` and `repo1-azure-key`.

### 6.2 Re-create the stanza

After changing the repository target, rebuild the stanza:

```bash
docker compose exec pgbackrest-cron pgbackrest stanza-create --stanza=main
docker compose exec pgbackrest-cron pgbackrest backup --stanza=main --type=full
```

### 6.3 Verify

```bash
docker compose exec pgbackrest-cron pgbackrest check --stanza=main
```

---

## 7. Backup verification (daily health check)

Two levels of verification are built in:

| Frequency | Command | What it checks |
|-----------|---------|----------------|
| Continuous | WAL archiving | Every 60 s, postgres archives WAL to the repository |
| Daily | `pgbackrest expire` | Enforces retention, removes stale WAL |
| Weekly | `pgbackrest check` | Reads backup files + verifies WAL chain (Sunday 03:00 UTC) |

**Manual spot-check:**

```bash
make backup-verify
```

Output includes each backup's size, WAL archive span, and status.

**Weekly restore drill:**

```bash
make test-restore
```

This is the strongest verification: it actually restores into an isolated
environment and compares row counts.  It runs automatically in CI every
week but you can run it any time (it creates and deletes its own isolated
Docker project).

---

## 8. RTO / RPO reference

| Metric | Target | How achieved |
|--------|--------|--------------|
| **RPO** (data loss) | ≤ 1 hour | WAL archived every 60 s (`archive_timeout=60`); full backup daily |
| **RTO** (recovery time) | ≤ 1 hour | `make restore-postgres` automates the restore; typical runtime 5-20 min depending on data size |

**RTO breakdown (typical):**

| Step | Time |
|------|------|
| Incident declared, team mobilised | 5-10 min |
| `make restore-postgres` runs | 5-15 min |
| `scripts/restore_verify.sh` passes | 2 min |
| App smoke-test by operator | 5 min |
| **Total** | **17-32 min** |

For databases > 100 GB, pgBackRest's delta restore (`--delta` flag, already
in the script) avoids re-copying unchanged files, significantly reducing RTO.
