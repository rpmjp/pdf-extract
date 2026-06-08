# pdf-extract

An enterprise-grade bank statement extraction platform. Upload scanned or digital PDFs, let a local or cloud LLM parse out account information and transactions, then route low-confidence documents to a human review queue. Every action is recorded in a tamper-evident audit log. Approved corrections feed back into the extraction pipeline as few-shot examples.

---

## Table of Contents

1. [What It Solves](#what-it-solves)
2. [Architecture Overview](#architecture-overview)
3. [Stack Choices and Rationale](#stack-choices-and-rationale)
4. [Data Model](#data-model)
5. [Document Lifecycle](#document-lifecycle)
6. [Extraction Pipeline](#extraction-pipeline)
7. [Confidence and Reconciliation](#confidence-and-reconciliation)
8. [Continuous Learning](#continuous-learning)
9. [Security Model](#security-model)
10. [API Reference](#api-reference)
11. [Frontend](#frontend)
12. [Monitoring and Observability](#monitoring-and-observability)
13. [Operations](#operations)
14. [Configuration Reference](#configuration-reference)
15. [Development Setup](#development-setup)
16. [Running Tests](#running-tests)
17. [Project Layout](#project-layout)

---

## What It Solves

Banks produce statements as PDFs. Processing them at scale — reconciling balances, extracting every transaction, validating totals — is labor-intensive when done manually and unreliable when done with naive string parsing. The format varies by institution: some are machine-readable (embedded text), others are scanned images. No single regex or template covers the space.

This project uses a vision-capable LLM as the extraction engine, wraps it with deterministic guardrails (balance reconciliation, sign correction, confidence scoring), stores every result immutably, and provides a human-in-the-loop review queue for anything the model is uncertain about. Human corrections are captured as structured examples and reused as few-shot context on future extractions from the same institution.

The system is built to be operated by a small team: one command to start, observable via Prometheus and Grafana out of the box, backed up continuously, and recoverable within an hour from a complete data-volume loss.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│  Browser (React + TypeScript + Vite)                        │
│  Dashboard · Review Queue · Document Detail · Insights      │
└───────────────┬────────────────────────────────────────────┘
                │ HTTP / REST
┌───────────────▼────────────────────────────────────────────┐
│  FastAPI (Python 3.12, Uvicorn)                            │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐  │
│  │ /auth    │ │/documents│ │ /review  │ │ /insights    │  │
│  │ /admin   │ │ /export  │ │ /jobs    │ │ /health/*    │  │
│  └──────────┘ └──────────┘ └──────────┘ └──────────────┘  │
└──────┬────────────────┬───────────────────────────────────┘
       │                │
  SQLAlchemy       Redis (Celery broker + result backend)
  psycopg3              │
       │                │
 ┌─────▼──────┐   ┌─────▼──────────────────────────────────┐
 │ PostgreSQL │   │  Celery Worker (concurrency=1)          │
 │ (data +    │   │  classify → LLM extract → reconcile →  │
 │  audit)    │   │  confidence → rules → learn            │
 └────────────┘   └──────┬────────────────────────────────┘
                         │
              ┌──────────▼──────────┐
              │  Ollama / OpenAI    │
              │  (qwen2.5vl:7b)    │
              └─────────────────────┘

 MinIO (object storage for PDFs)
 ClamAV (malware scan on upload)
 Prometheus + Grafana (metrics)
 pgBackRest (WAL archive + daily full backup)
 Promtail → Loki (structured log shipping, opt-in)
```

The API is stateless. Workers pull from a Redis queue. All durable state lives in Postgres (rows) and MinIO (files). Nothing is in-memory beyond request scope.

---

## Stack Choices and Rationale

### FastAPI over Flask or Django

FastAPI gives async-first routing with Pydantic validation at the boundary, OpenAPI docs generated automatically, and a dependency injection system that makes auth/session/rate-limit composable without middleware bloat. The project doesn't need Django's ORM or admin (we have our own) and doesn't need the weight.

### SQLAlchemy 2.x with psycopg3

SQLAlchemy's TypeDecorator gives us transparent column-level encryption ([`api/app/crypto.py`](api/app/crypto.py)) without changes to query code. psycopg3 is the modern asyncio-compatible Postgres driver; it supports binary protocol and is the recommended driver for SQLAlchemy 2.x on Python 3.12.

### Celery + Redis (not FastAPI background tasks)

FastAPI background tasks are tied to the request process. If the API restarts mid-parse, the job is lost. Celery tasks are durable: Redis stores the task message until a worker ACKs completion. Workers can be scaled independently of the API. The Redis result backend lets the API poll job state without a long-lived connection.

Worker concurrency is set to 1 per replica ([`docker-compose.yml:160`](docker-compose.yml#L160)). LLM inference is GPU-bound and sequential — two tasks competing for the same GPU thrash worse than taking turns. To increase throughput, add worker replicas; do not raise concurrency. See [Operations → Scaling](#scaling).

### Ollama with qwen2.5vl:7b (default)

A 7B vision-language model fits on a single consumer GPU (8 GB VRAM with 4-bit quantization) and handles both text extraction and image-based parsing through the same API surface. Swapping to OpenAI or any OpenAI-compatible endpoint is one environment variable (`LLM_BACKEND=openai`). The extraction code uses a standard chat-completion interface throughout; no vendor lock-in beyond the model-selection config.

### MinIO (S3-compatible object storage)

PDFs are not stored in Postgres (TOAST fragmentation, backup size, streaming complications). MinIO is S3-compatible, self-hosted, supports server-side encryption (AES-256 via built-in KMS), and the same boto3 client works against AWS S3 in production without code changes. A continuous mirror to a second MinIO instance (separate Docker volume) gives RPO ≈ seconds for object storage.

### React + TanStack Query + Tailwind

TanStack Query handles cache invalidation, background refetching, and loading states declaratively — the review queue and job polling patterns map directly to its `refetchInterval` and `invalidateQueries` API. Tailwind keeps styling co-located with markup and eliminates dead CSS. Vite's HMR makes the development loop fast enough that we don't need a separate design system.

### pgBackRest for Postgres backups

pgBackRest is the de-facto standard for Postgres backup management. It supports WAL archiving (continuous stream of changes), point-in-time recovery, parallel restore, and multiple repository backends (local filesystem, S3, GCS, Azure). The setup here uses a shared-volume sidecar pattern: the main postgres image archives WAL via `archive_command`, and a separate scheduler container triggers daily full backups without needing SSH access to the primary. See [Operations → Backup and Restore](#backup-and-restore).

---

## Data Model

The schema is managed with Alembic. Eleven migrations cover the full history from initial tables through security hardening and learning features.

### Core Tables

**`documents`** — One row per uploaded PDF.

| Column | Notes |
|--------|-------|
| `id` | Serial primary key |
| `filename` | Original upload filename |
| `sha256` | Content hash; used for deduplication |
| `minio_key` | Object key in MinIO bucket |
| `status` | State machine: `uploaded → queued → parsing → verified / needs_review / failed → approved / rejected` |
| `scan_status` | ClamAV result: `unscanned / clean / infected / scan_error` |
| `account_holder` | AES-256-GCM encrypted at rest |
| `account_number` | AES-256-GCM encrypted at rest |
| `statement_period` | Free-form string from extraction |
| `opening_balance` / `closing_balance` | Decimal; used for reconciliation |
| `confidence_score` | 0.0–1.0; deterministic heuristic, see [Confidence Scoring](#confidence-and-reconciliation) |
| `current_job_id` | FK to `parse_jobs.id` (UUID = Celery task id) |
| `deleted_at` | Set on retention tombstone; account fields cleared |
| `deletion_policy_id` | FK to active retention policy |

**`transactions`** — One row per line item extracted from a document.

| Column | Notes |
|--------|-------|
| `txn_date` | Date string as extracted |
| `description` | Narration text |
| `amount` | Decimal, always positive |
| `type` | `deposit` or `withdrawal` |
| `balance` | Running balance after this transaction |
| `confidence` | Per-transaction score |

**`audit_log`** — Append-only action history with SHA-256 hash chain.

| Column | Notes |
|--------|-------|
| `action` | `upload`, `parse`, `view_file`, `approve`, `reject`, `edit`, `export`, `retention_delete`, … |
| `actor` | Username of the acting user |
| `details` | JSONB; action-specific context |
| `hash_chain` | SHA-256 of `(previous_hash || action || details || actor || timestamp)` |

The chain starts from a genesis hash. `GET /admin/audit/verify` walks every row and re-derives each hash. Any gap or mismatch surfaces as a tampering indicator. See [`api/app/audit_chain.py`](api/app/audit_chain.py).

**`parse_jobs`** — One row per Celery task. The row id equals the Celery task UUID so status can be retrieved from either Celery's result backend or the database.

**`document_versions`** — Immutable extraction snapshots. Every parse result is written here with `source="llm_parse"`. Every approved edit creates a `source="review_edit"` snapshot. The original extraction is never overwritten; it's always recoverable.

**`correction_examples`** — Captures diffs between original LLM output and final approved version. Fields: `field_diffs` (JSON path → before/after), `failure_category` (sign_flip, amount_off_by_decimal, description_merged, …), `pdf_features` (bank, layout type, page count). Used for few-shot retrieval. See [`api/app/learning/`](api/app/learning/).

**`users`** / **`refresh_tokens`** — Auth tables. Users have a `roles` array. Refresh tokens store `token_hash` (bcrypt), not the raw token, plus `ip_address` for audit. Token rotation on every `/auth/refresh` call.

**`retention_policies`** — Configurable per-status rules (e.g., delete `rejected` documents after 90 days). Enforced daily by a Celery Beat task. Deletion is a tombstone: MinIO object deleted, account fields nulled, `deleted_at` set — the row and audit trail remain.

---

## Document Lifecycle

```
1. Upload
   POST /documents  (or /documents/batch for multi-file)
   ├── validate: .pdf extension, magic bytes (%PDF-), ≤50 MB
   ├── stream to temp file, hash while reading
   ├── SHA-256 dedup check → 409 Conflict if exists
   ├── ClamAV scan (TCP to clamd:3310)
   └── persist to MinIO + insert Document row (status=uploaded)

2. Queue
   POST /documents/{id}/parse
   ├── enqueue_parse_for_document() — idempotent; returns existing job if already pending
   ├── insert ParseJob row (status=queued)
   └── push Celery task to Redis

3. Parse (Worker)  [see Extraction Pipeline below]

4. Human Review (if status=needs_review or failed)
   GET /review-queue
   GET /documents/{id}          — view extraction, reconciliation, versions, audit log
   PATCH /documents/{id}/transactions/{txn_id}  — edit a transaction
   POST /documents/{id}/approve / reject

5. Approval Learning Capture
   POST /documents/{id}/approve
   ├── diff original LLM extraction vs current state
   ├── categorize failure mode
   ├── store CorrectionExample
   └── close open ReviewItems

6. Retention (automated)
   Celery Beat: daily enforce_retention task
   ├── match against active RetentionPolicies
   ├── delete from MinIO
   ├── tombstone Document row
   └── log audit entry
```

State transitions are enforced at the route layer. A document in `approved` status cannot be re-queued without explicit admin action.

---

## Extraction Pipeline

The extraction pipeline lives entirely in the Celery worker ([`api/app/worker.py`](api/app/worker.py)) with PDF handling in [`api/app/extract.py`](api/app/extract.py).

### Step 1: Classify the PDF

[`extract.py:classify_and_extract()`](api/app/extract.py) opens the file with pdfplumber. If it can extract more than 20 characters of text, the PDF is classified as **digital** (embedded text, no OCR needed). Otherwise it's **scanned**.

Digital PDFs: extract raw text + word bounding boxes directly.
Scanned PDFs: render each page to a 200 DPI base64 PNG via PyMuPDF (`fitz.Matrix(200/72, 200/72)`), then pass the images to the vision model.

### Step 2: LLM Extraction

The worker constructs a prompt asking the model to return a JSON object matching the `StatementExtraction` schema ([`api/app/schemas.py`](api/app/schemas.py)):

```python
class StatementExtraction(BaseModel):
    account_holder: str | None
    account_number: str | None
    statement_period: str | None
    opening_balance: float | None
    closing_balance: float | None
    transactions: list[Transaction]

class Transaction(BaseModel):
    date: str
    description: str
    amount: float   # always positive; negative values are auto-corrected
    type: Literal["deposit", "withdrawal"]
    balance: float | None
```

If `few_shot_enabled` is set, [`learning/fewshot.py:retrieve_few_shot()`](api/app/learning/fewshot.py) queries `CorrectionExample` for past examples from the same bank and layout type, and prepends them as user/assistant message pairs. Budget is capped by token estimate to stay within the model's context window.

### Step 3: Reconciliation and Sign Correction

[`reconcile.py:correct_signs_from_balances()`](api/app/reconcile.py) uses the running balance as ground truth. If the balance increases from one row to the next, the transaction must be a deposit — regardless of what the model said. This catches the most common LLM error on bank statements: misclassifying withdrawals as deposits.

[`reconcile.py:reconcile()`](api/app/reconcile.py) then checks the balance equation:

```
opening_balance + Σ(deposits) − Σ(withdrawals) = closing_balance   (tolerance ±0.01)
```

and verifies per-row balance continuity. If either check fails, the document is routed to `needs_review`.

### Step 4: Confidence Scoring

[`confidence.py`](api/app/confidence.py) applies a deterministic heuristic — no LLM self-assessment:

```
Per transaction:
  base = 0.94 if reconciliation passed else 0.72
  − 0.12 if PDF was scanned
  − 0.18 if balance field missing
  − 0.12 if description is blank
  − 0.08 if amount is zero

Document confidence = min(all transaction scores)
  − 0.15 if opening or closing balance missing
```

### Step 5: Ensemble Pass

If `ensemble_confidence_enabled` is set, the worker runs a second extraction with `variant="ensemble"` (different temperature / system prompt). [`confidence.py:extraction_disagreement()`](api/app/confidence.py) counts field-level mismatches between the two passes (amount, type, date, description for each transaction; header fields). Each mismatch reduces confidence:

```python
confidence -= min(0.35, disagreement_score × 0.7)
```

Ensemble disagreement is not a quality guarantee — it's an uncertainty signal. Two identical wrong answers score 0 disagreement. But in practice, genuine ambiguity in the source PDF causes the model to vary its output, and that variation is a reliable indicator that a human should look.

### Step 6: Rules Post-Processing

[`learning/rules.py:apply_rules()`](api/app/learning/rules.py) runs deterministic corrections after LLM output. The only enabled rule today is `recover_blank_descriptions`: it scans the original PDF text for lines matching the transaction's date and amount, then fills in blank description fields from that context. Each correction is counted and logged for observability.

### Step 7: Preprocessing Fallback

If confidence is still below 0.75 after all of the above, and the PDF is scanned, the worker re-renders each page through [`extract.py:preprocess_page_image()`](api/app/extract.py):

- **Deskew**: test rotations from −2° to +2° in 0.5° steps, keep the angle that maximizes row-darkness variance (a sharp horizontal grid of text pixels means rows are aligned)
- **Auto-contrast**: normalize pixel intensity histogram
- **Sharpen and median filter**: reduce noise before OCR

Then re-runs extraction on the cleaned images. If the model is still unavailable and `ocr_fallback_enabled` is set, Tesseract OCR runs as a final fallback.

### Step 8: Persist

On success:
- Transaction rows inserted
- `DocumentVersion` snapshot written (`source="llm_parse"`)
- `Document.confidence_score` updated
- `Document.status` set to `verified` (reconciliation passed) or `needs_review`
- `ParseJob.status` set to `success`
- Audit entry logged

On failure:
- `ParseJob.status` set to `failed`
- `Document.status` set to `failed`
- `ReviewItem` created for human triage
- Celery's `on_failure` hook fires via the custom `ParseTask` base class

---

## Confidence and Reconciliation

Confidence routing is the bridge between fully automated processing and the human review queue.

| Confidence | Reconciliation | Outcome |
|-----------|----------------|---------|
| ≥ 0.75 | passed | `verified` — no review required |
| ≥ 0.60 | passed | `verified` but P2 priority in dashboard |
| any | failed | `needs_review` (P1 if confidence < 0.60) |
| < 0.60 | passed | `needs_review` (P1) |

Priority is computed on every serialization by [`priority.py:compute_priority()`](api/app/priority.py) — it's never stored — so changing the routing rules takes effect immediately for all existing documents.

The balance reconciliation check in `reconcile()` is treated as a hard constraint, not advisory. A document that fails reconciliation always goes to review regardless of confidence. This is intentional: a high-confidence wrong answer is more dangerous than a low-confidence answer because it might pass unnoticed.

---

## Continuous Learning

### Correction Examples

When a reviewer approves a document, [`learning/examples.py:create_correction_example_for_document()`](api/app/learning/examples.py) computes the diff between the original `llm_parse` version and the current (corrected) state. The diff uses JSON path notation:

```
root.account_holder: "JOHN SMITH" → "John Smith"
transactions[2].type: "deposit" → "withdrawal"
transactions[2].amount: 150.00 → 1500.00
```

[`learning/categorize.py:categorize_failure()`](api/app/learning/categorize.py) classifies the primary failure mode in priority order: `sign_flip` → `amount_off_by_decimal` → `description_merged` → `description_split` → `date_wrong` → `balance_only` → `header_field` → `multi` → `other`.

This category appears in the `GET /admin/failures` endpoint, which shows failure trends by week and by bank — giving an operator visibility into whether the model is systematically wrong for a specific institution.

### Few-Shot Retrieval

On subsequent extractions, [`learning/fewshot.py:retrieve_few_shot()`](api/app/learning/fewshot.py) queries `CorrectionExample` hierarchically:

1. Same bank name AND same layout type (tabular/prose/mixed) — k=3 most recent
2. Same layout type only
3. Any example, k=3

The retrieved examples are formatted as user/assistant pairs and prepended to the extraction prompt. This is deliberately simple: no vector embeddings, no semantic search. For a bounded set of bank formats, recency + format similarity is sufficient signal, and the implementation has no external dependencies beyond the database already in use.

### A/B Evaluation Harness

The worker supports a `variant` parameter on extraction. The ensemble pass uses this to run a second independent extraction. The same mechanism can be used to evaluate prompt variants against each other: run both, log disagreement, compare error rates over a window. The harness exists in the worker and route layer; no UI yet.

---

## Security Model

### Authentication

JWT access tokens (15-minute expiry) + refresh tokens (7-day expiry, stored as bcrypt hashes in `refresh_tokens`). Token rotation on every refresh — a stolen token is invalidated on next use. `POST /auth/logout-all` revokes all sessions for a user immediately.

Three failed login attempts lock the account. Lockout is stored in Redis (TTL-based) and cleared by `POST /admin/users/{user_id}/unlock`.

JWT key rotation is supported via `JWT_PREVIOUS_SECRETS`: the token header carries a `kid` claim, and the API tries the current key first, then falls back to previous keys. Old tokens remain valid until they expire naturally; no forced re-login on rotation.

### Role-Based Access

| Role | Permissions |
|------|------------|
| `uploader` | Upload PDFs, trigger parse jobs, view own documents |
| `reviewer` | All uploader permissions + view all documents, edit transactions, approve/reject |
| `admin` | All reviewer permissions + audit verification, retention management, evidence export, user unlock |

Routes declare required roles via `Depends(require_roles("admin"))` in the FastAPI dependency. The dependency reads the JWT claims on every request; roles are not cached beyond the token lifetime.

### PII Encryption

`account_holder` and `account_number` are encrypted with AES-256-GCM before being written to Postgres, using a custom SQLAlchemy `TypeDecorator` ([`api/app/crypto.py`](api/app/crypto.py)):

```
Storage format: enc:v1:<base64(12-byte nonce || ciphertext || 16-byte GCM tag)>
```

A fresh 12-byte nonce is generated per encryption, making repeated-value ciphertext attacks impractical. The master key is `COLUMN_ENCRYPTION_KEY` (64 hex characters = 32 bytes = 256 bits). Legacy plaintext values are returned as-is (backward compatibility during migration); a one-time migration tool at `api/tools/encrypt_pii.py` re-encrypts existing rows.

Balances, dates, and amounts are stored plaintext — they're needed for aggregate queries (reconciliation, insights), and on their own they don't identify an individual without the encrypted name/number.

### Object Storage Encryption

MinIO server-side encryption is enabled via `MINIO_KMS_SECRET_KEY`. All objects are encrypted at rest using AES-256. The API passes no encryption headers — SSE is enforced at the MinIO layer so it can't be bypassed by client code.

For production deployments with key-custody requirements, the encryption.md operations doc describes switching to an external KMS (SSE-KMS mode).

### Malware Scanning

Every uploaded PDF is scanned by ClamAV via the INSTREAM protocol ([`api/app/security/scanner.py`](api/app/security/scanner.py)) before it's written to MinIO. An infected file returns HTTP 422 and is not persisted. If the ClamAV daemon is unreachable, upload proceeds with `scan_status=unscanned` — ClamAV availability is not a hard dependency for upload, but `rescan_documents` (hourly Celery Beat task) will retry. Operators can see unscanned documents in the admin view.

### Upload Validation

[`api/app/upload_helpers.py`](api/app/upload_helpers.py) validates uploads at multiple layers:
- File extension must be `.pdf`
- Content-Type must be `application/pdf`
- First 4 bytes must be `%PDF` (magic byte check, prevents spoofing the Content-Type)
- Size cap at 50 MB enforced during streaming — the file is never fully buffered in memory
- Rate limit: 50 uploads per user per hour (slowapi + Redis)

### Audit Chain

Every state-changing action appends a row to `audit_log`. The `hash_chain` column is:

```python
SHA-256(prev_hash + action + json(details) + actor + timestamp.isoformat())
```

An advisory lock (`SELECT pg_advisory_lock(1234)`) serializes concurrent inserts so the chain never forks. `GET /admin/audit/verify` walks the full chain and re-derives each hash; any mismatch identifies the row where tampering occurred.

### PII in Logs

[`api/app/logging_utils.py:PiiRedactionFilter`](api/app/logging_utils.py) is attached to all loggers at startup. It scrubs patterns matching `****1234` (masked account numbers) and bare digit strings ≥8 characters from every log message before emission. `pii_safe_log()` additionally accepts known holder names to replace with `[REDACTED-NAME]`. Production deployments set `LOG_FORMAT=json` to emit newline-delimited JSON; the `JsonFormatter` class attaches structured fields (`doc_id`, `user`, etc.) supplied via `extra={}` on individual log calls.

### Rate Limiting

slowapi enforces per-user limits on sensitive endpoints:
- `POST /auth/login` — 5 per 15 minutes
- `POST /documents` — 50 per hour
- `POST /documents/{id}/parse` — 20 per hour

Limits are backed by Redis and survive API restarts.

### CORS

`CORS_ORIGINS` is an explicit allowlist. In production, `validate_production_safety()` in [`api/app/config.py`](api/app/config.py) raises a startup error if `JWT_SECRET` is the default dev value or if `POSTGRES_PASSWORD` is weak, preventing accidentally deploying with development credentials.

---

## API Reference

All routes require a `Bearer` token in the `Authorization` header unless noted.

### Authentication

| Method | Path | Role | Description |
|--------|------|------|-------------|
| POST | `/auth/login` | — | Username/password → access + refresh token pair |
| POST | `/auth/refresh` | — | Rotate refresh token → new pair |
| POST | `/auth/logout` | any | Revoke current refresh token |
| POST | `/auth/logout-all` | any | Revoke all sessions for the current user |
| GET | `/auth/me` | any | Current user payload (id, username, roles) |

### Documents

| Method | Path | Role | Description |
|--------|------|------|-------------|
| POST | `/documents` | uploader | Upload a single PDF (streaming, 50/hr) |
| POST | `/documents/batch` | uploader | Upload multiple PDFs; per-file rollback |
| GET | `/documents` | reviewer | Paginated list with filter/sort/search |
| GET | `/documents/stats` | reviewer | Global counts: total, needs_review, processing, avg_confidence |
| POST | `/documents/bulk` | reviewer | Bulk reparse / approve / reject |
| GET | `/documents/{id}` | reviewer | Full document detail: transactions, jobs, versions, audit, review items |
| GET | `/documents/{id}/file` | reviewer | Stream PDF with Range header support |
| GET | `/documents/{id}/jobs` | reviewer | Parse job history for a document |
| GET | `/documents/{id}/versions` | reviewer | Immutable extraction version history |
| POST | `/documents/{id}/parse` | uploader | Queue or re-queue a parse job (20/hr) |
| PATCH | `/documents/{id}/transactions/{txn_id}` | reviewer | Edit a transaction field; recomputes reconciliation |
| POST | `/documents/{id}/approve` | reviewer | Approve document; captures correction example |
| POST | `/documents/{id}/reject` | reviewer | Reject with reason; adds ReviewItem |
| GET | `/documents/{id}/export` | admin | Download evidence ZIP (signed manifest) |
| POST | `/documents/{id}/extract` | reviewer | Diagnostic: run classify_and_extract without LLM |

#### Query Parameters for `GET /documents`

| Param | Values | Notes |
|-------|--------|-------|
| `q` | string | Full-text search on filename and account_holder |
| `filter` | `needs_review`, `processing` | Filter by status group |
| `priority` | `P1`, `P2`, `P3` | Filter by computed priority |
| `sort` | `id`, `filename`, `priority`, `status`, `confidence_score`, `created_at` | |
| `order` | `asc`, `desc` | |
| `page` | integer | |
| `per_page` | integer | |

### Evidence Export

`GET /documents/{id}/export` returns a `application/zip` stream containing:

| File | Contents |
|------|---------|
| `{filename}.pdf` | Original uploaded PDF from MinIO |
| `extraction_original.json` | First `llm_parse` DocumentVersion |
| `extraction_final.json` | Current extraction state |
| `transactions.csv` | date, description, amount, type, balance, confidence |
| `audit_log.json` | All audit entries for this document |
| `versions.json` | All DocumentVersion snapshots |
| `corrections.json` | CorrectionExample records |
| `parse_jobs.json` | Full parse job history |
| `manifest.json` | SHA-256 of each file + HMAC-SHA256 signature |

The manifest signature uses `HMAC-SHA256(key=audit_export_key, msg=canonical_json(sha256_map))`. Verifiers can re-derive the HMAC to confirm the archive has not been modified since export. If MinIO is unreachable at export time, the PDF entry is empty bytes; all other files still export.

### Review Queue

| Method | Path | Role | Description |
|--------|------|------|-------------|
| GET | `/review-queue` | reviewer | Documents needing attention; includes review_item_count and first reason |

### Insights

| Method | Path | Role | Description |
|--------|------|------|-------------|
| GET | `/insights/overview` | reviewer | KPIs, trends, alerts, low-confidence documents |

Query param `range`: `7d`, `30d`, `90d`, or `all`.

Response envelope:
- **kpis**: `pass_rate`, `mean_confidence`, `auto_approval_rate`, `median_review_time_hours` — each with delta vs previous equivalent period
- **alerts**: aged documents >24h, failure-rate spike ≥20%, mean confidence <70%
- **volume_vs_confidence**: scatter series for chart
- **pass_rate_trend**: weekly pass rate over the selected range
- **failure_breakdown**: counts by failure category from correction examples
- **by_source**: digital vs scanned document counts and mean confidence
- **queue_depth**: current Celery queue depth (Redis LLEN)
- **parse_latency**: p50/p95/p99 parse duration from ParseJob timestamps
- **low_confidence_docs**: bottom 20 documents by confidence score

### Admin

| Method | Path | Role | Description |
|--------|------|------|-------------|
| GET | `/admin/failures` | admin | Correction examples grouped by failure category, with weekly trends |
| GET | `/admin/audit/verify` | admin | Walk and verify the full audit hash chain |
| POST | `/admin/retention/dry-run` | admin | Preview which documents would be deleted by active retention policies |
| GET | `/admin/retention/policies` | admin | List active retention policies |
| POST | `/admin/users/{user_id}/unlock` | admin | Clear login lockout for a user |

### Health and Metrics

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/health/live` | — | Liveness: always 200 |
| GET | `/health/ready` | — | Readiness: 200 if Postgres + Redis + MinIO healthy, else 503 |
| GET | `/health/startup` | — | Startup: 200 when Alembic migrations are current, else 503 |
| GET | `/health` | — | Alias for `/health/live` |
| GET | `/health/deps` | — | Alias for `/health/ready` |
| GET | `/metrics` | — | Prometheus text format |

Kubernetes probe mapping:

```yaml
livenessProbe:
  httpGet: { path: /health/live, port: 8000 }
  periodSeconds: 10

readinessProbe:
  httpGet: { path: /health/ready, port: 8000 }
  periodSeconds: 5
  failureThreshold: 3

startupProbe:
  httpGet: { path: /health/startup, port: 8000 }
  failureThreshold: 30
  periodSeconds: 5
```

---

## Frontend

React 18 + TypeScript + Vite SPA. Communicates with the API over `VITE_API_URL`. All API calls go through [`web/src/api.ts`](web/src/api.ts), which attaches the Bearer token and handles 401 → redirect-to-login.

### Pages

| Route | Component | Purpose |
|-------|-----------|---------|
| `/` | `Dashboard` | Document table with filter cards (All / Needs Review / Processing), column sort, search, bulk actions, server-side pagination |
| `/documents/:id` | `DocumentPage` | Full detail: PDF viewer, editable transactions table, reconciliation panel, account card, version history, audit log timeline |
| `/review` | `ReviewQueuePage` | Priority-sorted list of documents needing human attention |
| `/insights/confidence` | `ConfidenceInsightsPage` | KPI cards, pass-rate trend chart, volume-vs-confidence scatter, failure breakdown, by-source table, low-confidence table |
| `/admin/failures` | `AdminFailuresPage` | Failure category analytics with weekly trends |
| `/login` | `LoginPage` | Username/password form |

### Key Components

| Component | File | Notes |
|-----------|------|-------|
| `AccountCard` | `components/AccountCard.tsx` | Displays decrypted account holder and number |
| `AuditLog` | `components/AuditLog.tsx` | Timeline of all audit entries for a document |
| `ConfidenceMeter` | `components/ConfidenceMeter.tsx` | Visual 0–100% score bar with color coding |
| `Dropzone` | `components/Dropzone.tsx` | Drag-and-drop upload with file validation feedback |
| `EditableTransactionsTable` | `components/EditableTransactionsTable.tsx` | In-place cell editing; PATCH on blur; recomputes reconciliation display |
| `PdfViewer` | `components/PdfViewer.tsx` | Embeds PDF via `<iframe>` + Range request streaming from `/documents/{id}/file` |
| `ReconciliationPanel` | `components/ReconciliationPanel.tsx` | Shows the balance equation and pass/fail for each check |
| `ReviewControls` | `components/ReviewControls.tsx` | Approve button + Reject button with reason input |
| `StatusBadge` | `components/StatusBadge.tsx` | Color-coded status chip |
| `TransactionsTable` | `components/TransactionsTable.tsx` | Read-only transaction display |

### State Management

No global state store. TanStack Query caches all server state with automatic background refetch. Parse job polling uses `refetchInterval: 2000` while a job is in-flight, switching to `false` once terminal. Document list invalidates on any write operation.

Auth state is kept in `App.tsx` context (access token + user info). On mount, the app attempts `GET /auth/me` with any stored token; if it fails, the token is cleared and the user is redirected to login.

---

## Monitoring and Observability

### Prometheus Metrics

Prometheus scrapes `GET /metrics` every 15 seconds ([`ops/prometheus/prometheus.yml`](ops/prometheus/prometheus.yml)). Custom metrics defined in [`api/app/prom.py`](api/app/prom.py):

| Metric | Type | Labels |
|--------|------|--------|
| `pdf_extract_request_count_total` | Counter | `method`, `path`, `status` |
| `pdf_extract_request_latency_seconds` | Histogram | `method`, `path` |
| `pdf_extract_queue_depth` | Gauge | — |

Latency histograms are recorded via middleware in `main.py`. Queue depth is sampled from the Celery Redis queue via `celery_queue_depth()`.

### Grafana

Grafana is pre-provisioned with dashboards from `ops/grafana/`. Access at `http://localhost:3000` (admin/admin). Dashboards cover:
- Request throughput and latency percentiles
- Queue depth over time
- Parse success/failure rates
- Document status distribution

### Structured Logging

Set `LOG_FORMAT=json` in production. The `JsonFormatter` emits one JSON object per line:

```json
{
  "timestamp": "2026-06-07T14:23:01.456Z",
  "level": "INFO",
  "logger": "app.worker",
  "message": "parse completed",
  "doc_id": 142,
  "confidence": 0.87,
  "source": "digital",
  "duration_ms": 3421
}
```

PII redaction runs before any handler emits the line — no account numbers or raw names appear in logs.

### Log Shipping (optional)

Start Promtail with `docker compose --profile logging up -d promtail`. It uses Docker socket discovery to find running containers, parses JSON log lines from those with the `LOG_FORMAT=json` config, and ships to Loki (`LOKI_ENDPOINT` env var). Labels: `container`, `service`, `job=pdf-extract`. Configuration: [`ops/promtail/promtail.yml`](ops/promtail/promtail.yml).

Retention policy (from [`docs/operations/health.md`](docs/operations/health.md)):
- Hot tier (Loki): 30 days
- Cold tier (object storage): 1 year

---

## Operations

### Backup and Restore

PostgreSQL backup uses pgBackRest with WAL archiving. Architecture:

- **postgres container**: Custom image ([`ops/postgres/Dockerfile`](ops/postgres/Dockerfile)) that includes pgBackRest and supercronic. Postgres starts with `wal_level=replica`, `archive_mode=on`, and `archive_command='pgbackrest --stanza=main archive-push %p'`. WAL segments are pushed to the backup repository volume (`pgbackups`) immediately after they complete, and at most 60 seconds after the last write (`archive_timeout=60`).

- **pgbackrest-cron sidecar**: Same image, different entrypoint ([`ops/postgres/pgbackrest-cron-entrypoint.sh`](ops/postgres/pgbackrest-cron-entrypoint.sh)). Waits for postgres to be ready, creates the stanza if it doesn't exist, then runs supercronic against [`ops/postgres/backup-schedule`](ops/postgres/backup-schedule):
  - `0 2 * * *` — full backup
  - `5 2 * * *` — expire backups older than 7 days
  - `0 3 * * 0` — pgBackRest integrity check

MinIO backup uses continuous mirroring. `minio-sync` runs `mc mirror --watch primary/documents replica/documents-backup` continuously, so RPO for object storage is approximately seconds.

**RTO ≤ 1 hour. RPO ≤ 1 hour** (Postgres WAL archived every 60s; MinIO mirrored in near-real-time).

#### Restore Commands

```bash
# One-command Postgres restore (interactive, with PITR options)
make restore-postgres

# One-command MinIO restore
make restore-minio

# Verify restore state
scripts/restore_verify.sh

# Weekly automated restore test (also runs as CI job, see .github/workflows/restore-test.yml)
make test-restore
```

`make test-restore` spins up an isolated Docker Compose project, seeds 5 known documents, takes a full backup, destroys the data volume, runs pgBackRest restore, restarts Postgres, and verifies that `COUNT(*)` before equals `COUNT(*)` after. No production state is touched.

Full step-by-step runbook: [`docs/operations/restore.md`](docs/operations/restore.md).

### Scaling

Workers are the only horizontally scalable component. The API is stateless and can be load-balanced. Postgres and MinIO scale vertically.

**Why concurrency=1**: LLM inference with a 7B model uses the full GPU. Running two concurrent tasks on one GPU causes thrashing (context switching at the CUDA level) and increases average latency without increasing throughput. One task at a time per worker replica maximizes GPU utilization.

**When to scale out**:
- Celery queue depth > 5 for 5+ minutes → add a replica
- Queue depth < 1 for 30+ minutes → remove a replica (floor: 2)
- p95 parse latency > 5 minutes → check Ollama saturation before adding workers

```bash
# Scale workers at runtime (no restart required for API or other services)
docker compose scale worker=3

# Full production stack with replicas pre-configured
docker compose -f docker-compose.yml -f docker-compose.production.yml up -d
```

[`docker-compose.production.yml`](docker-compose.production.yml) sets `worker.deploy.replicas=2` as a starting point.

Full scaling guidance: [`docs/operations/scaling.md`](docs/operations/scaling.md).

### Health Probes

| Probe | Endpoint | Checks | Action on Failure |
|-------|----------|--------|-------------------|
| Liveness | `/health/live` | Always passes | Kubernetes restarts container |
| Readiness | `/health/ready` | Postgres + Redis + MinIO reachable | Kubernetes removes from load balancer |
| Startup | `/health/startup` | Postgres reachable + Alembic migration current | Kubernetes waits (up to 150s) before switching to liveness/readiness |

Docker Compose healthcheck on the API service uses `/health/ready` (`interval: 10s, retries: 5`).

Details: [`docs/operations/health.md`](docs/operations/health.md).

### Secrets

Production secrets:

| Variable | Purpose | Rotation procedure |
|----------|---------|-------------------|
| `JWT_SECRET` | Sign access tokens | Add old value to `JWT_PREVIOUS_SECRETS`, update `JWT_SECRET`, deploy — old tokens remain valid until expiry |
| `JWT_KEY_ID` | `kid` header for rotation tracking | Update alongside `JWT_SECRET` |
| `POSTGRES_PASSWORD` | Database auth | Update external secret → restart postgres + api + worker containers → verify `/health/ready` |
| `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` | Object storage auth | Create new key in MinIO console → deploy with new values → test → revoke old key |
| `COLUMN_ENCRYPTION_KEY` | PII column encryption | New key encrypts new writes; existing rows remain readable (legacy plaintext tolerance) → run `api/tools/encrypt_pii.py` to re-encrypt existing rows → remove old key |
| `AUDIT_EXPORT_KEY` | HMAC for evidence export signatures | Falls back to `JWT_SECRET` if not set |

Full rotation procedures: [`docs/operations/secrets.md`](docs/operations/secrets.md).

### Encryption at Rest

Two independent layers:

1. **Column-level** (Postgres): `account_holder` and `account_number` are AES-256-GCM encrypted before storage. Key: `COLUMN_ENCRYPTION_KEY`. Implementation: [`api/app/crypto.py`](api/app/crypto.py).

2. **Object-level** (MinIO): All PDF objects are AES-256 encrypted by MinIO's built-in KMS, keyed by `MINIO_KMS_SECRET_KEY`. No application code change required — SSE is enforced at the storage layer.

For key-custody separation in regulated environments, both layers can be migrated to external KMS (HashiCorp Vault, AWS KMS, GCP KMS). The MinIO SSE-S3 → SSE-KMS migration procedure is documented in [`docs/operations/encryption.md`](docs/operations/encryption.md).

---

## Configuration Reference

All settings are read from environment variables via pydantic-settings ([`api/app/config.py`](api/app/config.py)). A `.env` file is supported for local development.

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_ENV` | `development` | Set to `production` to enforce security checks |
| `DATABASE_URL` | constructed from parts | Full Postgres DSN |
| `POSTGRES_HOST` / `_PORT` / `_USER` / `_PASSWORD` / `_DB` | — | Used to build DATABASE_URL |
| `REDIS_URL` | constructed | Redis DSN for Celery broker |
| `REDIS_RESULT_URL` | constructed | Redis DSN for Celery result backend |
| `MINIO_ENDPOINT` | constructed | MinIO host:port |
| `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` | — | MinIO credentials |
| `MINIO_BUCKET` | `documents` | Bucket name |
| `MINIO_USE_SSL` | `false` | Enable TLS for MinIO connection |
| `MINIO_KMS_SECRET_KEY` | — | Enables MinIO SSE-S3 AES-256 encryption |
| `CLAMAV_HOST` | `clamav` | ClamAV daemon hostname |
| `CLAMAV_PORT` | `3310` | ClamAV daemon port |
| `JWT_SECRET` | — | Required; production rejects default dev value |
| `JWT_KEY_ID` | `v1` | `kid` header value for key rotation tracking |
| `JWT_PREVIOUS_SECRETS` | — | Comma-separated old secrets; validated before rejection |
| `COLUMN_ENCRYPTION_KEY` | — | 64 hex chars (32 bytes); required for PII encryption |
| `AUDIT_EXPORT_KEY` | — | HMAC key for evidence ZIP; falls back to `JWT_SECRET` |
| `LLM_BACKEND` | `ollama` | `ollama` or `openai` |
| `OLLAMA_BASE_URL` | `http://host.docker.internal:11434` | Ollama API endpoint |
| `OLLAMA_MODEL` | `qwen2.5vl:7b` | Model name |
| `OPENAI_API_KEY` | — | Required when `LLM_BACKEND=openai` |
| `CORS_ORIGINS` | `["http://localhost:5173"]` | Allowlist for CORS |
| `LOG_FORMAT` | `text` | Set to `json` for structured logging |
| `FEW_SHOT_ENABLED` | `true` | Enable few-shot retrieval from correction examples |
| `ENSEMBLE_CONFIDENCE_ENABLED` | `true` | Enable second extraction pass for disagreement scoring |
| `OCR_FALLBACK_ENABLED` | `true` | Enable Tesseract fallback for low-confidence scanned PDFs |
| `CLAMAV_ENABLED` | `true` | Set to `false` to skip ClamAV (not recommended in production) |
| `LOKI_ENDPOINT` | `http://loki:3100` | Used by Promtail (logging profile only) |

---

## Development Setup

**Prerequisites**: Docker, Docker Compose, Ollama with `qwen2.5vl:7b` pulled (or set `LLM_BACKEND=openai` with an API key).

```bash
# 1. Clone and copy env template
git clone <repo>
cd pdf-extract
cp .env.example .env
# Edit .env — set POSTGRES_PASSWORD, MINIO credentials, JWT_SECRET, COLUMN_ENCRYPTION_KEY

# 2. Start all services
docker compose up -d

# 3. Run database migrations
docker compose exec api alembic upgrade head

# 4. (Optional) Create a dev user
docker compose exec api python -c "
from app.db import SessionLocal
from app.models import User
from passlib.context import CryptContext
db = SessionLocal()
pwd = CryptContext(schemes=['bcrypt'])
db.add(User(username='admin', password_hash=pwd.hash('admin123'), roles=['uploader','reviewer','admin']))
db.commit()
"

# 5. Open the UI
open http://localhost:5173
```

Services and their default ports:

| Service | Port |
|---------|------|
| API (FastAPI) | 8003 (configurable via `API_PORT`) |
| Web (Vite) | 5173 |
| PostgreSQL | 5435 (configurable via `POSTGRES_PORT`) |
| Redis | 6379 (configurable via `REDIS_PORT`) |
| MinIO API | 9000 |
| MinIO Console | 9001 |
| Prometheus | 9090 |
| Grafana | 3000 |
| ClamAV | 3310 |

**Hot reload**: The `api` and `worker` services mount `./api:/app` as a volume. `uvicorn --reload` restarts the API on file changes. The worker does **not** auto-reload — restart it manually after worker code changes: `docker compose restart worker`.

**Without Docker** (API only):

```bash
cd api
pip install -r requirements.txt
export DATABASE_URL="postgresql+psycopg://user:pass@localhost:5435/pdf_extract"
export REDIS_URL="redis://localhost:6379/0"
# ... other env vars
uvicorn app.main:app --reload
```

---

## Running Tests

```bash
# All backend tests
cd api
pip install -r requirements.txt
POSTGRES_PASSWORD=<your-dev-password> python -m pytest

# Specific test files
python -m pytest tests/test_documents.py -v
python -m pytest tests/test_auth.py -v
python -m pytest tests/test_export.py -v     # evidence ZIP + signature
python -m pytest tests/test_health.py -v     # probe split
python -m pytest tests/test_audit.py -v      # hash chain

# Frontend type check (no test runner yet)
cd web
npx tsc --noEmit

# End-to-end (Playwright)
cd web
npx playwright test

# Restore test (isolated, no production state touched)
make test-restore
```

Test configuration is in [`api/tests/conftest.py`](api/tests/conftest.py). Tests spin up against a real Postgres instance (not mocked) — the test DB URL defaults to `localhost:5435` with `POSTGRES_PASSWORD`. Isolation is via per-test transactions rolled back after each test.

CI runs the full suite on every push and PR. The restore test runs as a weekly cron (`0 4 * * 0` UTC Sunday) via [`.github/workflows/restore-test.yml`](.github/workflows/restore-test.yml) and can also be triggered manually via `workflow_dispatch`.

---

## Project Layout

```
pdf-extract/
├── api/
│   ├── app/
│   │   ├── main.py               # FastAPI app factory, middleware, router registration
│   │   ├── config.py             # Pydantic settings (all env vars)
│   │   ├── models.py             # SQLAlchemy ORM models
│   │   ├── schemas.py            # Pydantic extraction schemas (input/output)
│   │   ├── serializers.py        # Centralized JSON serialization helpers
│   │   ├── db.py                 # Engine + SessionLocal factory
│   │   ├── auth.py               # JWT encode/decode, role dependency
│   │   ├── crypto.py             # AES-256-GCM EncryptedString TypeDecorator
│   │   ├── audit_chain.py        # Hash-chained audit log insert + verify
│   │   ├── worker.py             # Celery tasks (parse pipeline, beat tasks)
│   │   ├── extract.py            # PDF classification, OCR, image rendering
│   │   ├── reconcile.py          # Balance validation and sign correction
│   │   ├── confidence.py         # Deterministic confidence scoring
│   │   ├── priority.py           # P1/P2/P3 computed priority
│   │   ├── prom.py               # Prometheus metric definitions
│   │   ├── logging_utils.py      # JsonFormatter, PiiRedactionFilter
│   │   ├── app_limiter.py        # slowapi rate limiter setup
│   │   ├── upload_helpers.py     # Validation, streaming, dedup, ClamAV
│   │   ├── celery_helpers.py     # Job state reconciliation, idempotent enqueue
│   │   ├── query_helpers.py      # Filter, sort, paginate helpers
│   │   ├── login_helpers.py      # Account lockout, rate limit helpers
│   │   ├── routes/
│   │   │   ├── auth.py           # /auth/* endpoints
│   │   │   ├── documents.py      # /documents/* endpoints
│   │   │   ├── review.py         # /review-queue endpoint
│   │   │   ├── jobs.py           # /jobs/{id} endpoint
│   │   │   ├── insights.py       # /insights/overview endpoint
│   │   │   ├── admin.py          # /admin/* endpoints
│   │   │   ├── export.py         # /documents/{id}/export endpoint
│   │   │   └── health.py         # /health/* + /metrics endpoints
│   │   ├── security/
│   │   │   └── scanner.py        # ClamAV INSTREAM scanner
│   │   └── learning/
│   │       ├── examples.py       # Correction example capture + diff
│   │       ├── fewshot.py        # Few-shot retrieval + prompt construction
│   │       ├── rules.py          # Deterministic post-processing rules
│   │       └── categorize.py     # Failure mode classification
│   ├── alembic/
│   │   └── versions/             # 11 migration files
│   ├── tests/
│   │   ├── conftest.py
│   │   ├── test_auth.py
│   │   ├── test_documents.py
│   │   ├── test_jobs.py
│   │   ├── test_export.py
│   │   ├── test_health.py
│   │   ├── test_audit.py
│   │   ├── test_security.py
│   │   └── test_logging_redaction.py
│   └── requirements.txt
├── web/
│   ├── src/
│   │   ├── App.tsx               # Shell, auth bootstrapping, routing
│   │   ├── api.ts                # All API calls, token attachment
│   │   ├── pages/
│   │   │   ├── Dashboard.tsx
│   │   │   ├── DocumentPage.tsx
│   │   │   ├── LoginPage.tsx
│   │   │   ├── ReviewQueuePage.tsx
│   │   │   ├── ConfidenceInsightsPage.tsx
│   │   │   ├── AdminFailuresPage.tsx
│   │   │   └── UploadPage.tsx
│   │   └── components/
│   │       ├── AccountCard.tsx
│   │       ├── AuditLog.tsx
│   │       ├── ConfidenceMeter.tsx
│   │       ├── Dropzone.tsx
│   │       ├── EditableTransactionsTable.tsx
│   │       ├── PdfViewer.tsx
│   │       ├── ReconciliationPanel.tsx
│   │       ├── ReviewControls.tsx
│   │       ├── StatusBadge.tsx
│   │       ├── TransactionsTable.tsx
│   │       └── UploadResult.tsx
│   └── tests/e2e/
│       └── app.spec.ts           # Playwright end-to-end tests
├── ops/
│   ├── postgres/
│   │   ├── Dockerfile            # postgres:16 + pgBackRest + supercronic
│   │   ├── pgbackrest.conf       # Backup configuration (posix repo, 7-day retention)
│   │   ├── pgbackrest-cron-entrypoint.sh
│   │   ├── initdb-pgbackrest.sh  # Stanza creation on first init
│   │   └── backup-schedule       # Supercronic crontab
│   ├── prometheus/
│   │   └── prometheus.yml        # Scrape config
│   ├── promtail/
│   │   └── promtail.yml          # Docker SD + JSON log pipeline
│   └── grafana/
│       ├── provisioning/         # Datasource + dashboard provisioning
│       └── dashboards/           # Pre-built Grafana dashboard JSON
├── docs/operations/
│   ├── restore.md                # Backup architecture + full restore runbook
│   ├── scaling.md                # Worker scaling rules and procedures
│   ├── health.md                 # Probe documentation + k8s YAML + log retention
│   ├── encryption.md             # Column + object encryption, key migration
│   └── secrets.md                # Secret rotation procedures for all credentials
├── scripts/
│   ├── test_restore.sh           # 9-step isolated restore test
│   └── restore_verify.sh         # Post-restore health checks
├── Makefile                      # backup, restore, test-restore, verify targets
├── docker-compose.yml            # Full service stack
├── docker-compose.production.yml # Production overrides (worker replicas)
├── .env.example                  # Template with all required variables
└── .github/workflows/
    ├── ci.yml                    # Test suite on push/PR
    └── restore-test.yml          # Weekly restore drill (Sunday 04:00 UTC)
```

---

## Key Design Decisions (for Contributors)

**Deterministic confidence over LLM self-assessment.** LLM confidence scores are not calibrated: a model can be 95% confident about a wrong answer. Our confidence metric uses observable signals only — field presence, reconciliation pass/fail, source type, ensemble disagreement. Changing the confidence formula requires only changing `confidence.py`; no model retraining needed.

**Reconciliation as a hard routing constraint.** Balance equations are math. If the extracted transactions don't sum to the stated opening and closing balances (within ±0.01), something is wrong — either the extraction missed a transaction, or misclassified a sign. The document goes to review. There is no threshold or override here.

**Append-only history everywhere.** `DocumentVersion`, `AuditLog`, and `ParseJob` rows are never updated after creation. `Transaction` rows can be edited by reviewers, but each edit creates a new `DocumentVersion` snapshot and an audit entry. The original LLM output is always recoverable.

**Column-level encryption, not full-disk.** Full-disk encryption protects against physical media theft but not against a compromised application. Column-level encryption means that even with direct SQL read access to the database, account holder names and numbers are ciphertext. Balances and dates are not encrypted because they're needed for SQL aggregation (reconciliation checks, insights queries).

**Few-shot over fine-tuning.** Fine-tuning a model requires a GPU training environment, a labeled dataset of sufficient size, and a deployment pipeline for model weights. Few-shot retrieval requires a database query and some prompt engineering. For a bounded set of bank statement formats, a curated set of correction examples as context outperforms a zero-shot prompt with much lower operational complexity.

**Sidecar backup pattern, not backup-from-primary.** Running pgBackRest backups from inside the postgres container creates a naming conflict (the image's entrypoint would need modification) and couples backup scheduling to the primary service's lifecycle. The sidecar shares only the data volume and connects via TCP for checkpoint control — it's independently restartable and uses the same image, reducing build complexity.
