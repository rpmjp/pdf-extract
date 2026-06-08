"""
Health and readiness endpoints.

  /health/live     — liveness probe   : always 200 while the process is running
  /health/ready    — readiness probe  : 200 when DB, Redis, and MinIO are all reachable
  /health/startup  — startup probe    : 200 when migrations have been applied
  /health          — alias for /health/live (backwards-compatible)
  /health/deps     — alias for /health/ready  (backwards-compatible)
  /metrics         — Prometheus scrape endpoint
"""
import logging

import redis as redis_lib
from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from ..celery_helpers import celery_queue_depth
from ..config import settings
from ..db import engine
from ..prom import QUEUE_DEPTH
from ..storage import s3

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Health"])


def _check_postgres() -> str:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return "ok"
    except Exception as exc:
        return f"error: {exc.__class__.__name__}"


def _check_redis() -> str:
    try:
        r = redis_lib.Redis(host=settings.redis_host, port=settings.redis_port, socket_timeout=2)
        r.ping()
        return "ok"
    except Exception as exc:
        return f"error: {exc.__class__.__name__}"


def _check_minio() -> str:
    try:
        s3.list_buckets()
        return "ok"
    except Exception as exc:
        return f"error: {exc.__class__.__name__}"


def _check_migrations() -> str:
    """Verify that alembic_version has at least one row (migrations applied)."""
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text("SELECT version_num FROM alembic_version LIMIT 1")
            ).fetchone()
        if result is None:
            return "error: no migrations applied"
        return f"ok: {result[0]}"
    except Exception as exc:
        return f"error: {exc.__class__.__name__}"


# ── Liveness ──────────────────────────────────────────────────────────────────

@router.get("/health/live", status_code=200)
def health_live():
    """Liveness probe. Returns 200 as long as the process is running.

    Kubernetes: use as livenessProbe. If this fails, the container is dead
    (OOM, deadlock) and should be restarted immediately.
    """
    return {"status": "ok"}


# Backwards-compatible alias
@router.get("/health", status_code=200, include_in_schema=False)
def health():
    return {"status": "ok"}


# ── Readiness ─────────────────────────────────────────────────────────────────

@router.get("/health/ready")
def health_ready(response: Response):
    """Readiness probe. Returns 200 only when all downstream dependencies are
    reachable (Postgres, Redis, MinIO).

    Kubernetes: use as readinessProbe. If this returns 503, remove the pod from
    the load balancer rotation until dependencies recover — do NOT restart.
    Docker: use as the healthcheck for the api service.
    """
    deps = {
        "postgres": _check_postgres(),
        "redis": _check_redis(),
        "minio": _check_minio(),
    }
    all_ok = all(v == "ok" for v in deps.values())
    if not all_ok:
        response.status_code = 503
        logger.warning("readiness check failed: %s", deps)
    return {"status": "ok" if all_ok else "degraded", "deps": deps}


# Backwards-compatible alias that was previously /health/deps
@router.get("/health/deps", include_in_schema=False)
def health_deps(response: Response):
    return health_ready(response)


# ── Startup ───────────────────────────────────────────────────────────────────

@router.get("/health/startup")
def health_startup(response: Response):
    """Startup probe. Returns 200 once the database is reachable and all
    schema migrations have been applied.

    Kubernetes: use as startupProbe with a generous failureThreshold (e.g. 30)
    and periodSeconds=5 to allow time for migrations to run on first deploy.
    Once this probe passes, Kubernetes switches to the liveness/readiness probes.
    Docker: not typically used — the api service healthcheck covers readiness.
    """
    checks = {
        "postgres": _check_postgres(),
        "migrations": _check_migrations(),
    }
    all_ok = all(v.startswith("ok") for v in checks.values())
    if not all_ok:
        response.status_code = 503
        logger.warning("startup check failed: %s", checks)
    return {"status": "ok" if all_ok else "not_ready", "checks": checks}


# ── Metrics ───────────────────────────────────────────────────────────────────

@router.get("/metrics")
def metrics():
    try:
        QUEUE_DEPTH.set(celery_queue_depth())
    except Exception:
        QUEUE_DEPTH.set(-1)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
