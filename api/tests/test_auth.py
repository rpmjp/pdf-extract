from conftest import main
from app.auth import AuthUser, create_access_token, decode_access_token
from app.config import Settings
from app.login_helpers import is_account_locked, redis_client
from app.models import User


def clear_security_redis():
    client = redis_client()
    for pattern in ("*LIMITER*", "login-failures:*", "login-lock:*"):
        for key in client.scan_iter(pattern):
            client.delete(key)


def test_login_returns_token_and_user(client):
    clear_security_redis()
    response = client.post("/auth/login", json={"username": "reviewer", "password": "review123"})

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["user"]["username"] == "reviewer"
    assert "reviewer" in body["user"]["roles"]


def test_protected_endpoint_requires_token(client):
    response = client.get("/documents")

    assert response.status_code == 401


def test_role_guard_blocks_reviewer_action_for_uploader(client, db):
    token = create_access_token(AuthUser(username="pytest-uploader", roles=["uploader"]))
    response = client.post("/documents/123/approve", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 403


def test_query_token_can_be_disabled(client, monkeypatch):
    token = create_access_token(AuthUser(username="pytest-reviewer", roles=["reviewer", "uploader"]))
    monkeypatch.setattr(main.settings, "allow_query_token_auth", False)

    response = client.get(f"/documents?token={token}")

    assert response.status_code == 401


def test_production_safety_rejects_dev_defaults(monkeypatch):
    monkeypatch.setattr(main.settings, "app_env", "production")
    monkeypatch.setattr(main.settings, "jwt_secret", "dev-" + "secret-change-me")
    monkeypatch.setattr(main.settings, "postgres_password", "dev" + "password")
    monkeypatch.setattr(main.settings, "minio_access_key", "minio" + "admin")
    monkeypatch.setattr(main.settings, "minio_secret_key", "minio" + "admin")
    monkeypatch.setattr(main.settings, "seed_dev_users", True)
    monkeypatch.setattr(main.settings, "allow_query_token_auth", True)
    monkeypatch.setattr(main.settings, "cors_origins", "*")

    try:
        main.settings.validate_production_safety()
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("production safety validation should fail")

    assert "JWT_SECRET" in message
    assert "POSTGRES_PASSWORD" in message
    assert "MINIO_ACCESS_KEY" in message
    assert "MINIO_SECRET_KEY" in message
    assert "SEED_DEV_USERS" in message
    assert "ALLOW_QUERY_TOKEN_AUTH" in message
    assert "CORS_ORIGINS" in message


def test_production_safety_rejects_missing_required_secrets():
    settings = Settings(
        app_env="production",
        jwt_secret=None,
        postgres_password=None,
        minio_access_key=None,
        minio_secret_key=None,
        seed_dev_users=False,
        allow_query_token_auth=False,
        cors_origins="https://bank.example",
    )

    try:
        settings.validate_production_safety()
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("production safety validation should fail")

    assert "JWT_SECRET must be set" in message
    assert "POSTGRES_PASSWORD must be set" in message
    assert "MINIO_ACCESS_KEY must be set" in message
    assert "MINIO_SECRET_KEY must be set" in message


def test_production_safety_accepts_strong_env_config():
    settings = Settings(
        app_env="production",
        jwt_secret="prod-jwt-secret-with-enough-length-001",
        postgres_password="prod-postgres-password-strong-001",
        minio_access_key="prod-minio-access-001",
        minio_secret_key="prod-minio-secret-strong-001",
        seed_dev_users=False,
        allow_query_token_auth=False,
        cors_origins="https://bank.example",
    )

    settings.validate_production_safety()


def test_access_token_accepts_previous_jwt_secret(monkeypatch):
    old_secret = "old-jwt-secret-with-enough-length-001"
    new_secret = "new-jwt-secret-with-enough-length-001"
    monkeypatch.setattr(main.settings, "jwt_secret", old_secret)
    monkeypatch.setattr(main.settings, "jwt_key_id", "old")
    monkeypatch.setattr(main.settings, "jwt_previous_secrets", "")
    token = create_access_token(AuthUser(username="pytest-reviewer", roles=["reviewer"]))

    monkeypatch.setattr(main.settings, "jwt_secret", new_secret)
    monkeypatch.setattr(main.settings, "jwt_key_id", "new")
    monkeypatch.setattr(main.settings, "jwt_previous_secrets", f"old:{old_secret}")

    user = decode_access_token(token)

    assert user.username == "pytest-reviewer"


def test_login_rate_limit_fires_at_threshold(client):
    clear_security_redis()

    for index in range(5):
        response = client.post("/auth/login", json={"username": f"pytest-rate-{index}", "password": "bad"})
        assert response.status_code == 401

    limited = client.post("/auth/login", json={"username": "pytest-rate-final", "password": "bad"})
    assert limited.status_code == 429
    assert "Retry-After" in limited.headers


def test_failed_login_lockout_and_admin_unlock(client, db):
    clear_security_redis()

    for index in range(5):
        response = client.post("/auth/login", json={"username": "reviewer", "password": "bad"})
    assert response.status_code == 423
    assert "Account locked" in response.json()["detail"]

    admin_token = create_access_token(AuthUser(username="pytest-admin", roles=["admin", "reviewer", "uploader"]))
    user = db.query(User).filter_by(username="reviewer").one()
    unlock = client.post(
        f"/admin/users/{user.id}/unlock",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert unlock.status_code == 200
    assert is_account_locked("reviewer") == 0


def test_refresh_flow_rotates_token_and_logout_invalidates(client):
    clear_security_redis()
    login = client.post("/auth/login", json={"username": "reviewer", "password": "review123"})
    assert login.status_code == 200
    original_refresh = login.json()["refresh_token"]

    refreshed = client.post("/auth/refresh", json={"refresh_token": original_refresh})
    assert refreshed.status_code == 200
    assert refreshed.json()["access_token"]
    next_refresh = refreshed.json()["refresh_token"]
    assert next_refresh != original_refresh

    reused = client.post("/auth/refresh", json={"refresh_token": original_refresh})
    assert reused.status_code == 401

    logout = client.post(
        "/auth/logout",
        headers={"Authorization": f"Bearer {refreshed.json()['access_token']}"},
        json={"refresh_token": next_refresh},
    )
    assert logout.status_code == 200
    assert logout.json()["revoked"] is True

    after_logout = client.post("/auth/refresh", json={"refresh_token": next_refresh})
    assert after_logout.status_code == 401


def test_logout_all_invalidates_user_refresh_tokens(client):
    clear_security_redis()
    first = client.post("/auth/login", json={"username": "reviewer", "password": "review123"}).json()
    second = client.post("/auth/login", json={"username": "reviewer", "password": "review123"}).json()

    response = client.post("/auth/logout-all", headers={"Authorization": f"Bearer {first['access_token']}"})

    assert response.status_code == 200
    assert response.json()["revoked"] >= 2
    assert client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]}).status_code == 401
    assert client.post("/auth/refresh", json={"refresh_token": second["refresh_token"]}).status_code == 401
