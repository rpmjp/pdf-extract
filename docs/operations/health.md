# Health Endpoints

The API exposes three distinct health probes, each designed for a different
orchestrator use-case.  Mapping them to the correct probe type is critical:
using the wrong one causes either unnecessary restarts or pods that stay in
rotation while unhealthy.

---

## Endpoints

### `GET /health/live` — Liveness

Returns `200 OK` as long as the process is running.

```json
{"status": "ok"}
```

**When it fails:** only if the process is dead (OOM-killed, segfault, deadlock
where the event loop is frozen).

**Orchestrator use:**
- **Kubernetes `livenessProbe`** — restart the container when this fails.
- **Docker Compose** — do not use as a healthcheck; use `/health/ready` instead.

```yaml
# Kubernetes livenessProbe
livenessProbe:
  httpGet:
    path: /health/live
    port: 8000
  initialDelaySeconds: 10
  periodSeconds: 15
  failureThreshold: 3
```

---

### `GET /health/ready` — Readiness

Returns `200 OK` when all downstream dependencies (Postgres, Redis, MinIO)
are reachable.  Returns `503 Service Unavailable` if any dependency is down.

```json
{
  "status": "ok",
  "deps": {
    "postgres": "ok",
    "redis": "ok",
    "minio": "ok"
  }
}
```

When a dependency is unhealthy:

```json
{
  "status": "degraded",
  "deps": {
    "postgres": "error: OperationalError",
    "redis": "ok",
    "minio": "ok"
  }
}
```

**When it fails:** database restart, Redis blip, MinIO volume unmounted.

**Orchestrator use:**
- **Kubernetes `readinessProbe`** — remove the pod from load balancer rotation
  until dependencies recover.  *Do NOT restart* — the pod itself is fine.
- **Docker Compose healthcheck** — use this so `depends_on: condition: service_healthy`
  waits for real readiness, not just process start.

```yaml
# Kubernetes readinessProbe
readinessProbe:
  httpGet:
    path: /health/ready
    port: 8000
  initialDelaySeconds: 5
  periodSeconds: 10
  failureThreshold: 3

# Docker Compose
healthcheck:
  test: ["CMD", "python", "-c",
    "import urllib.request; r = urllib.request.urlopen('http://localhost:8000/health/ready', timeout=2); exit(0 if r.status == 200 else 1)"]
  interval: 10s
  timeout: 3s
  retries: 5
  start_period: 20s
```

---

### `GET /health/startup` — Startup

Returns `200 OK` once the database is reachable **and** all Alembic migrations
have been applied.  Returns `503` until that condition is met.

```json
{
  "status": "ok",
  "checks": {
    "postgres": "ok",
    "migrations": "ok: ab12cd34ef56"
  }
}
```

**When it fails:** immediately after deployment, before the migration job
finishes; also if the database connection is not yet available.

**Orchestrator use:**
- **Kubernetes `startupProbe`** — Kubernetes checks this until it passes (or
  `failureThreshold` is exceeded), then switches to `livenessProbe` /
  `readinessProbe`.  Set a generous `failureThreshold` to allow time for
  migrations to run on first deploy.

```yaml
# Kubernetes startupProbe
startupProbe:
  httpGet:
    path: /health/startup
    port: 8000
  initialDelaySeconds: 5
  periodSeconds: 5
  failureThreshold: 30   # 30 × 5s = 2.5 min allowed for migrations
```

- **Docker Compose / local dev** — not required (migrations run via `alembic upgrade head`
  in the CI/CD pipeline before starting the service).

---

## Backwards-compatible aliases

| Old path | Alias for |
|----------|-----------|
| `GET /health` | `/health/live` |
| `GET /health/deps` | `/health/ready` |

These aliases are preserved so existing monitoring scripts and Docker healthcheck
configs do not break.

---

## Decision tree for orchestrators

```
Pod starts
 │
 ├─ startupProbe (/health/startup)
 │    │  Polls every 5 s until migrations applied (up to 2.5 min)
 │    └─ PASS ──► switch to livenessProbe + readinessProbe
 │
 ├─ livenessProbe (/health/live)
 │    │  Polls every 15 s
 │    └─ FAIL × 3 ──► restart container
 │
 └─ readinessProbe (/health/ready)
      │  Polls every 10 s
      └─ FAIL ──► remove from load balancer (no restart)
           │
           └─ PASS (deps recovered) ──► add back to load balancer
```

---

## Log aggregator health

If you are running Promtail for log shipping (see `docker compose --profile logging`),
Promtail exposes its own HTTP endpoint on port 9080:

```
GET http://promtail:9080/ready    → Promtail scrape loop running
GET http://promtail:9080/metrics  → Prometheus metrics for Promtail itself
```

Monitor `promtail_targets_active_total` in Grafana to confirm log shipping is active.

---

## Log retention policy

| Tier | Duration | Storage | Query performance |
|------|----------|---------|-------------------|
| Hot  | 30 days  | Loki in-cluster / fast disk | Full-text, label query, instant |
| Cold | 1 year   | Object storage (S3 / GCS)   | Available but slower (minutes) |

Configure in your Loki `limits_config`:

```yaml
# loki-config.yaml
limits_config:
  retention_period: 8760h   # 1 year total
  # Hot/cold tiering via storage_config.aws or storage_config.gcs
  # See Loki docs: https://grafana.com/docs/loki/latest/operations/storage/
```

For compliance, 1 year of cold log retention covers most financial audit
requirements (SOX, PCI-DSS Level 2).  Adjust to your jurisdiction.
