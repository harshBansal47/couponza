import pytest


@pytest.mark.asyncio
async def test_register_and_login_flow(client):
    register_resp = await client.post(
        "/api/v1/auth/register",
        json={"email": "user@example.com", "password": "supersecret123"},
    )
    assert register_resp.status_code == 201
    body = register_resp.json()
    assert body["email"] == "user@example.com"
    assert body["role"] == "user"

    login_resp = await client.post(
        "/api/v1/auth/login",
        data={"username": "user@example.com", "password": "supersecret123"},
    )
    assert login_resp.status_code == 200
    tokens = login_resp.json()
    assert "access_token" in tokens
    assert "refresh_token" in tokens


@pytest.mark.asyncio
async def test_duplicate_registration_rejected(client):
    payload = {"email": "dupe@example.com", "password": "supersecret123"}
    first = await client.post("/api/v1/auth/register", json=payload)
    assert first.status_code == 201

    second = await client.post("/api/v1/auth/register", json=payload)
    assert second.status_code == 400


@pytest.mark.asyncio
async def test_login_rejects_wrong_password(client):
    await client.post(
        "/api/v1/auth/register",
        json={"email": "wrongpw@example.com", "password": "supersecret123"},
    )
    resp = await client.post(
        "/api/v1/auth/login",
        data={"username": "wrongpw@example.com", "password": "not-the-password"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_requires_auth(client):
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_returns_current_user(client):
    await client.post(
        "/api/v1/auth/register",
        json={"email": "me@example.com", "password": "supersecret123"},
    )
    login_resp = await client.post(
        "/api/v1/auth/login",
        data={"username": "me@example.com", "password": "supersecret123"},
    )
    token = login_resp.json()["access_token"]

    resp = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["email"] == "me@example.com"


@pytest.mark.asyncio
async def test_admin_only_endpoint_forbidden_for_regular_user(client):
    await client.post(
        "/api/v1/auth/register",
        json={"email": "regular@example.com", "password": "supersecret123"},
    )
    login_resp = await client.post(
        "/api/v1/auth/login",
        data={"username": "regular@example.com", "password": "supersecret123"},
    )
    token = login_resp.json()["access_token"]

    resp = await client.get("/api/v1/auth/admin-ping", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_only_endpoint_allowed_for_admin(client, async_session_maker):
    from app.core.security import create_access_token
    from app.models.user import Role
    from app.schemas.user import UserCreate
    from app.services import user_service

    async with async_session_maker() as session:
        admin = await user_service.create_user(
            session,
            UserCreate(email="admin@example.com", password="supersecret123"),
            role=Role.admin,
        )
        token = create_access_token(str(admin.id), admin.role.value)

    resp = await client.get("/api/v1/auth/admin-ping", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert "admin@example.com" in resp.json()["message"]


@pytest.mark.asyncio
async def test_refresh_token_issues_new_access_token(client):
    await client.post(
        "/api/v1/auth/register",
        json={"email": "refresh@example.com", "password": "supersecret123"},
    )
    login_resp = await client.post(
        "/api/v1/auth/login",
        data={"username": "refresh@example.com", "password": "supersecret123"},
    )
    refresh_token = login_resp.json()["refresh_token"]

    resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert resp.status_code == 200
    assert "access_token" in resp.json()


@pytest.mark.asyncio
async def test_access_token_rejected_by_refresh_endpoint(client):
    login_payload = {"email": "wrongtype@example.com", "password": "supersecret123"}
    await client.post("/api/v1/auth/register", json=login_payload)
    login_resp = await client.post(
        "/api/v1/auth/login",
        data={"username": "wrongtype@example.com", "password": "supersecret123"},
    )
    access_token = login_resp.json()["access_token"]

    resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": access_token})
    assert resp.status_code == 401
