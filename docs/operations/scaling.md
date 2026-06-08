# Worker Autoscaling

## Why concurrency=1

Each Celery worker process calls the LLM (Ollama by default) for PDF extraction.
Ollama runs inference sequentially on a single GPU or CPU core — sending two
requests simultaneously does not speed things up; it causes memory contention
and typically makes *both* requests slower.

The correct model is: **one task at a time per LLM instance**, scaled by running
more worker replicas (each consuming one queue slot at a time).

```
             Redis (broker)
             └── pdf-extract-queue
                  ├── Task A ──→ worker-1 (concurrency=1)  ──→ Ollama
                  ├── Task B ──→ worker-2 (concurrency=1)  ──→ Ollama
                  └── Task C  (waiting)
```

If Ollama can handle parallel requests (e.g., you switch to an API provider
with real parallelism), raise `--concurrency` and/or add more replicas.

---

## Horizontal scaling rules

| Condition | Action |
|-----------|--------|
| Queue depth > 5, sustained for 5+ minutes | Add one more worker replica |
| Queue depth < 1 for 30+ minutes (off-peak) | Remove one replica (floor = 2) |
| p95 task latency > 5 min | Add replicas AND check if Ollama is saturated |
| Worker OOM-killed | Reduce concurrency (already 1) or increase container memory limit |

**Queue depth** is visible in:
- Grafana dashboard → "Jobs queued / day" panel
- `docker compose exec redis redis-cli LLEN celery`

---

## Production: 2 worker replicas (default)

`docker-compose.production.yml` sets `deploy.replicas: 2`, giving two workers
ready to process tasks in parallel.

**Start with 2 replicas:**

```bash
docker compose -f docker-compose.yml -f docker-compose.production.yml up -d
```

**Scale to N replicas at runtime (no restart required):**

```bash
docker compose -f docker-compose.yml -f docker-compose.production.yml \
  scale worker=N
```

**Check current workers and queue:**

```bash
# Active workers
docker compose exec worker celery -A app.worker inspect active

# Queue depth
docker compose exec redis redis-cli LLEN celery

# Scheduled / reserved tasks
docker compose exec worker celery -A app.worker inspect reserved
```

---

## Development: single worker

`docker-compose.yml` (dev stack) runs a single worker.  You can test
multi-worker behaviour locally by adding `--scale worker=2`:

```bash
docker compose up --scale worker=2
```

---

## Tuning Ollama throughput

If the LLM is the bottleneck (not queue depth, but high latency per task):

1. **Larger GPU memory** — run a bigger model context (`OLLAMA_NUM_CTX`).
2. **Batch requests** — the current extraction pipeline sends one document
   at a time; batching multiple pages in one LLM call reduces round-trips.
3. **Model selection** — `qwen2.5vl:7b` balances quality vs. speed.
   Switch to `qwen2.5vl:3b` for lower latency at slight accuracy cost,
   or to a hosted API (set `LLM_BACKEND=openai`) for elastic scaling.

---

## Future: event-driven autoscaling

For fully automated autoscaling, connect a queue-depth metric to a scaling
controller:

- **Docker Swarm** — use a queue-depth metric from Prometheus + Swarm
  `--constraint` rules to auto-scale the `worker` service.
- **Kubernetes** — KEDA (Kubernetes Event-Driven Autoscaling) can watch
  the Redis queue depth directly and scale a `Deployment` up/down.

The current concurrency policy (`concurrency=1`, horizontal replicas) is
compatible with both approaches without any worker code changes.
