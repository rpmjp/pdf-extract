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
