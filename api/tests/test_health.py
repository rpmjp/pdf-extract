"""
Tests for the split health endpoints:

  /health/live    — always 200
  /health/ready   — 200 when all deps ok, 503 when any dep fails
  /health/startup — 200 when migrations applied, 503 otherwise
  /health         — alias for /health/live (backwards-compat)
  /health/deps    — alias for /health/ready (backwards-compat)
"""
from unittest.mock import patch, MagicMock

import pytest
import sqlalchemy.exc


# ── Liveness ──────────────────────────────────────────────────────────────────

def test_live_always_returns_200(client):
    r = client.get("/health/live")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_health_alias_returns_200(client):
    """Backwards-compatible /health must still work."""
    r = client.get("/health")
    assert r.status_code == 200


# ── Readiness ─────────────────────────────────────────────────────────────────

def test_ready_returns_200_when_all_deps_ok(client):
    """All deps reachable → 200 with status=ok."""
    with patch("app.routes.health._check_postgres", return_value="ok"), \
         patch("app.routes.health._check_redis", return_value="ok"), \
         patch("app.routes.health._check_minio", return_value="ok"):
        r = client.get("/health/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["deps"]["postgres"] == "ok"
    assert body["deps"]["redis"] == "ok"
    assert body["deps"]["minio"] == "ok"


def test_ready_503_when_postgres_down(client):
    with patch("app.routes.health._check_postgres", return_value="error: OperationalError"), \
         patch("app.routes.health._check_redis", return_value="ok"), \
         patch("app.routes.health._check_minio", return_value="ok"):
        r = client.get("/health/ready")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "degraded"
    assert "error" in body["deps"]["postgres"]


def test_ready_503_when_redis_down(client):
    with patch("app.routes.health._check_postgres", return_value="ok"), \
         patch("app.routes.health._check_redis", return_value="error: ConnectionError"), \
         patch("app.routes.health._check_minio", return_value="ok"):
        r = client.get("/health/ready")
    assert r.status_code == 503
    assert r.json()["status"] == "degraded"


def test_ready_503_when_minio_down(client):
    with patch("app.routes.health._check_postgres", return_value="ok"), \
         patch("app.routes.health._check_redis", return_value="ok"), \
         patch("app.routes.health._check_minio", return_value="error: EndpointConnectionError"):
        r = client.get("/health/ready")
    assert r.status_code == 503
    assert "error" in r.json()["deps"]["minio"]


def test_ready_includes_all_dep_names(client):
    with patch("app.routes.health._check_postgres", return_value="ok"), \
         patch("app.routes.health._check_redis", return_value="ok"), \
         patch("app.routes.health._check_minio", return_value="ok"):
        r = client.get("/health/ready")
    deps = r.json()["deps"]
    assert set(deps.keys()) == {"postgres", "redis", "minio"}


def test_deps_alias_same_as_ready(client):
    """Backwards-compatible /health/deps must behave identically to /health/ready."""
    with patch("app.routes.health._check_postgres", return_value="ok"), \
         patch("app.routes.health._check_redis", return_value="ok"), \
         patch("app.routes.health._check_minio", return_value="ok"):
        r_deps  = client.get("/health/deps")
        r_ready = client.get("/health/ready")
    assert r_deps.status_code == r_ready.status_code
    assert r_deps.json()["status"] == r_ready.json()["status"]


# ── Startup ───────────────────────────────────────────────────────────────────

def test_startup_returns_200_when_migrations_applied(client):
    """Real test DB has migrations applied — startup must return 200."""
    with patch("app.routes.health._check_postgres", return_value="ok"), \
         patch("app.routes.health._check_migrations", return_value="ok: abc123"):
        r = client.get("/health/startup")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["checks"]["postgres"] == "ok"


def test_startup_503_when_no_migrations(client):
    with patch("app.routes.health._check_postgres", return_value="ok"), \
         patch("app.routes.health._check_migrations", return_value="error: no migrations applied"):
        r = client.get("/health/startup")
    assert r.status_code == 503
    assert r.json()["status"] == "not_ready"
    assert "error" in r.json()["checks"]["migrations"]


def test_startup_503_when_postgres_down(client):
    with patch("app.routes.health._check_postgres", return_value="error: OperationalError"), \
         patch("app.routes.health._check_migrations", return_value="ok: abc123"):
        r = client.get("/health/startup")
    assert r.status_code == 503


def test_startup_response_contains_migration_version(client):
    """The migration version string should appear in the checks payload."""
    with patch("app.routes.health._check_postgres", return_value="ok"), \
         patch("app.routes.health._check_migrations", return_value="ok: deadbeef1234"):
        r = client.get("/health/startup")
    assert "deadbeef1234" in r.json()["checks"]["migrations"]


# ── No auth required for health ───────────────────────────────────────────────

def test_health_endpoints_require_no_auth(client):
    """All health endpoints must be publicly accessible (no auth header)."""
    for path in ("/health/live", "/health/ready", "/health/startup", "/health", "/health/deps"):
        with patch("app.routes.health._check_postgres", return_value="ok"), \
             patch("app.routes.health._check_redis", return_value="ok"), \
             patch("app.routes.health._check_minio", return_value="ok"), \
             patch("app.routes.health._check_migrations", return_value="ok: v1"):
            r = client.get(path)
        assert r.status_code in (200, 503), f"{path} returned unexpected {r.status_code}"
