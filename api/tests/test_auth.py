from conftest import main


def test_login_returns_token_and_user(client):
    response = client.post("/auth/login", json={"username": "reviewer", "password": "review123"})

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["user"]["username"] == "reviewer"
    assert "reviewer" in body["user"]["roles"]


def test_protected_endpoint_requires_token(client):
    response = client.get("/documents")

    assert response.status_code == 401


def test_role_guard_blocks_reviewer_action_for_uploader(client, db):
    token = main.create_access_token(main.AuthUser(username="pytest-uploader", roles=["uploader"]))
    response = client.post("/documents/123/approve", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 403


def test_query_token_can_be_disabled(client, monkeypatch):
    token = main.create_access_token(main.AuthUser(username="pytest-reviewer", roles=["reviewer", "uploader"]))
    monkeypatch.setattr(main.settings, "allow_query_token_auth", False)

    response = client.get(f"/documents?token={token}")

    assert response.status_code == 401


def test_production_safety_rejects_dev_defaults(monkeypatch):
    monkeypatch.setattr(main.settings, "app_env", "production")
    monkeypatch.setattr(main.settings, "jwt_secret", "dev-secret-change-me")
    monkeypatch.setattr(main.settings, "seed_dev_users", True)
    monkeypatch.setattr(main.settings, "allow_query_token_auth", True)

    try:
        main.settings.validate_production_safety()
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("production safety validation should fail")

    assert "JWT_SECRET" in message
    assert "SEED_DEV_USERS" in message
    assert "ALLOW_QUERY_TOKEN_AUTH" in message
