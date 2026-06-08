# Encryption at Rest

Two independent layers protect sensitive data: MinIO server-side encryption (SSE-S3) covers every stored PDF, and AES-256-GCM column-level encryption covers PII fields in Postgres.

---

## 1. MinIO SSE-S3 (Object Storage)

### How it works

MinIO's built-in KMS is activated by the `MINIO_KMS_SECRET_KEY` environment variable. When the key is set, MinIO generates a unique Data Encryption Key (DEK) for each object using the master key as the wrapping key. The DEK is stored alongside the object; the master key never touches disk.

The API sets `ServerSideEncryption: AES256` on every `PUT` request and applies a bucket-level default encryption policy on startup, so objects written without an explicit SSE header are still encrypted.

**v1 (current):** SSE-S3 with MinIO's built-in single-key KMS.  
**v2 (planned):** SSE-KMS with an external KMS (HashiCorp Vault or AWS KMS), providing full key-custody separation, audit logging, and automatic key rotation at the KMS layer.

### Configuration

```bash
# .env — generate a fresh 32-byte key and base64-encode it:
#   python -c "import os,base64; print('my-key:'+base64.b64encode(os.urandom(32)).decode())"
MINIO_KMS_SECRET_KEY=my-minio-key:<base64-encoded-32-byte-key>

# Disable SSE (dev/test only):
MINIO_SSE_ENABLED=false
```

The `MINIO_KMS_SECRET_KEY` environment variable must be set on the MinIO container **before** it starts. Changing the key without migrating existing objects renders them unreadable.

### Verifying encryption

After uploading a document, locate the raw on-disk file inside the MinIO data volume:

```bash
# Find the object on disk (path pattern: /data/<bucket>/<key>/xl.meta + part.1)
docker compose exec minio find /data/documents -name "part.1" | head -5

# Confirm the bytes are NOT a readable PDF:
docker compose exec minio xxd /data/documents/<sha256>.pdf/part.1 | head -4
# Expected: binary garbage, not '%PDF-'
```

If SSE is active the raw bytes will show no PDF magic header.

---

## 2. Postgres Column-Level Encryption (PII)

### What is encrypted

| Column | Table | Rationale |
|---|---|---|
| `account_holder` | `documents` | Full name — PII |
| `account_number` | `documents` | Bank identifier — PII |

All other fields (`statement_period`, balances, confidence scores) are stored in plaintext.

### Algorithm

**AES-256-GCM** via Python's `cryptography` library (`EncryptedString` SQLAlchemy TypeDecorator in `api/app/crypto.py`).

- A 12-byte random nonce is generated per value, per write.
- Authenticated encryption (GCM tag) detects any tampering.
- Stored format: `enc:v1:<base64(nonce || ciphertext || tag)>`

The `enc:v1:` prefix enables a legacy-fallback path: if a stored value does not start with the prefix, the TypeDecorator returns it as plaintext. This lets existing rows co-exist with encrypted rows during a rolling migration.

### pgcrypto extension

The migration enables `CREATE EXTENSION IF NOT EXISTS pgcrypto` for future DB-side operations (e.g., `pgp_sym_encrypt` in SQL functions, triggers, or audit procedures). The current Python-level implementation does not call pgcrypto functions, but the extension is in place if needed.

### Configuration

```bash
# .env — generate a 32-byte key as hex (never commit a real key):
#   python -c "import os; print(os.urandom(32).hex())"
COLUMN_ENCRYPTION_KEY=<64-hex-character-string>
```

The key is read at process start. If `COLUMN_ENCRYPTION_KEY` is absent, any attempt to write or read an encrypted column raises `RuntimeError` at the application layer.

### Migration procedure

The Alembic migration `b7c8d9e0f1a2` performs the following:

1. `CREATE EXTENSION IF NOT EXISTS pgcrypto`
2. Widens `account_holder` and `account_number` from `VARCHAR` to `TEXT` (ciphertext is always longer than plaintext).
3. Adds `scan_status VARCHAR(16) DEFAULT 'unscanned'` to `documents`.
4. Re-encrypts existing plaintext rows using the key from `COLUMN_ENCRYPTION_KEY`.

Run the migration with the key set:

```bash
COLUMN_ENCRYPTION_KEY=<key> python -m alembic upgrade head
# or via Docker Compose:
docker compose exec -e COLUMN_ENCRYPTION_KEY=<key> api python -m alembic upgrade head
```

If the key is unavailable at migration time, existing plaintext rows are left untouched. Run `api/tools/encrypt_pii.py` (see below) to encrypt them later.

### Encrypting remaining plaintext rows (post-migration)

```bash
# tools/encrypt_pii.py — re-encrypts any row where account_holder or account_number
# does not yet start with "enc:v1:"
COLUMN_ENCRYPTION_KEY=<key> python api/tools/encrypt_pii.py
```

---

## 3. Key Rotation

### MinIO SSE-S3 key rotation

MinIO does not currently support transparent re-encryption on key change for SSE-S3 objects (unlike SSE-KMS with external KMS). The procedure is:

1. Deploy new MinIO instance (or restart) with the new `MINIO_KMS_SECRET_KEY`.
2. For each existing object: download → re-upload. The re-upload encrypts with the new DEK under the new master key.
3. Confirm all objects are re-encrypted, then retire the old key.

**v2 target:** Use SSE-KMS with HashiCorp Vault. Vault supports automatic key rotation and re-encryption without object re-upload.

### Postgres column encryption key rotation

1. Generate a new 32-byte key and set `COLUMN_ENCRYPTION_KEY_NEW=<new-key>` alongside the existing `COLUMN_ENCRYPTION_KEY=<old-key>`.
2. Run the rotation script (double-decrypt with old key, re-encrypt with new key):

```bash
# Not yet implemented — planned for v2. Manual procedure in the interim:
# 1. Read all rows, decrypt with old key, encrypt with new key, write back.
# 2. Swap COLUMN_ENCRYPTION_KEY to the new key.
# 3. Redeploy.
```

The `enc:v1:` version prefix in the stored format allows a future migration to add `enc:v2:` and support mixed-key reads during the rotation window.

---

## 4. What Is NOT Encrypted

| Data | Reason |
|---|---|
| `statement_period` | Date range, not directly identifying |
| `opening_balance`, `closing_balance` | Financial figures without account link |
| `filename` | May contain names — considered metadata, not PII per current data model |
| Transactions | Linked only via `document_id`; amounts/dates without account context are lower-risk |
| Audit log | Operational data; actor usernames are stored in plaintext |

Review this classification as your data-privacy obligations evolve.
