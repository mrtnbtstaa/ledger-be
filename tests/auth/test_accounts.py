from uuid import UUID

from app.config import get_settings
from app.core.passwords import verify_password
from app.db.models import User
from app.db.session import SessionLocal
from app.repos import users as user_repo

PASSWORD = "correct horse battery"


async def test_register_normalizes_email_and_hashes_password(client) -> None:
    response = await client.post(
        "/auth/register", json={"email": " Owner@Example.com ", "password": PASSWORD}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["user"]["email"] == "owner@example.com"
    assert body["accessToken"]
    assert body["refreshToken"]
    assert body["expiresIn"] > 0

    async with SessionLocal() as session:
        user = await user_repo.get_by_email(session, "owner@example.com")
        assert user is not None
        assert user.password_hash.startswith("$argon2id$")
        assert PASSWORD not in user.password_hash
        assert verify_password(PASSWORD, user.password_hash)


async def test_duplicate_email_is_rejected(client) -> None:
    first = await client.post(
        "/auth/register", json={"email": "owner@example.com", "password": PASSWORD}
    )
    assert first.status_code == 201

    duplicate = await client.post(
        "/auth/register", json={"email": " OWNER@example.com", "password": PASSWORD}
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "email_taken"


async def test_weak_passwords_are_rejected(client) -> None:
    for email, password in (
        ("short@example.com", "short"),
        ("long@example.com", "a" * 129),
        ("same@example.com", "same@example.com"),
    ):
        response = await client.post(
            "/auth/register", json={"email": email, "password": password}
        )
        assert response.status_code == 422, email
        assert response.json()["error"]["code"] == "weak_password"


async def test_login_failures_are_generic(client) -> None:
    await client.post(
        "/auth/register", json={"email": "owner@example.com", "password": PASSWORD}
    )

    wrong = await client.post(
        "/auth/login", json={"email": "owner@example.com", "password": "wrong password"}
    )
    unknown = await client.post(
        "/auth/login", json={"email": "nobody@example.com", "password": PASSWORD}
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["error"]["code"] == unknown.json()["error"]["code"] == "invalid_credentials"
    assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]

    success = await client.post(
        "/auth/login", json={"email": "OWNER@example.com", "password": PASSWORD}
    )
    assert success.status_code == 200
    assert success.json()["accessToken"]


async def test_refresh_rotates_and_reuse_revokes_family(client, monkeypatch) -> None:
    tokens = (
        await client.post(
            "/auth/register", json={"email": "owner@example.com", "password": PASSWORD}
        )
    ).json()

    refreshed = await client.post(
        "/auth/refresh", json={"refreshToken": tokens["refreshToken"]}
    )
    assert refreshed.status_code == 200
    rotated = refreshed.json()
    assert rotated["refreshToken"] != tokens["refreshToken"]

    monkeypatch.setattr(get_settings(), "refresh_reuse_grace_seconds", 0)
    reused = await client.post(
        "/auth/refresh", json={"refreshToken": tokens["refreshToken"]}
    )
    assert reused.status_code == 401
    assert reused.json()["error"]["code"] == "refresh_invalid"

    family_revoked = await client.post(
        "/auth/refresh", json={"refreshToken": rotated["refreshToken"]}
    )
    assert family_revoked.status_code == 401


async def test_refresh_grace_allows_two_tabs(client) -> None:
    tokens = (
        await client.post(
            "/auth/register", json={"email": "owner@example.com", "password": PASSWORD}
        )
    ).json()

    first = (
        await client.post("/auth/refresh", json={"refreshToken": tokens["refreshToken"]})
    ).json()
    sibling = await client.post(
        "/auth/refresh", json={"refreshToken": tokens["refreshToken"]}
    )
    assert sibling.status_code == 200

    still_valid = await client.post(
        "/auth/refresh", json={"refreshToken": first["refreshToken"]}
    )
    assert still_valid.status_code == 200


async def test_logout_revokes_the_family(client) -> None:
    tokens = (
        await client.post(
            "/auth/register", json={"email": "owner@example.com", "password": PASSWORD}
        )
    ).json()

    logout = await client.post(
        "/auth/logout", json={"refreshToken": tokens["refreshToken"]}
    )
    assert logout.status_code == 204

    after = await client.post(
        "/auth/refresh", json={"refreshToken": tokens["refreshToken"]}
    )
    assert after.status_code == 401


async def test_me_and_usage(client, owner_tokens, owner_headers) -> None:
    denied = await client.get("/me")
    assert denied.status_code == 401
    assert denied.json()["error"]["code"] == "unauthenticated"

    me = await client.get("/me", headers=owner_headers)
    assert me.status_code == 200
    assert me.json()["email"] == "owner@example.com"
    assert me.json()["id"] == owner_tokens["user"]["id"]

    usage = await client.get("/me/usage", headers=owner_headers)
    assert usage.status_code == 200
    assert usage.json()["uploads"] == 0
    assert usage.json()["limit"] == get_settings().user_daily_upload_limit


async def test_delete_account_requires_password_and_removes_access(
    client, owner_tokens, owner_headers
) -> None:
    wrong = await client.request(
        "DELETE", "/me", json={"password": "wrong password"}, headers=owner_headers
    )
    assert wrong.status_code == 401
    assert (await client.get("/me", headers=owner_headers)).status_code == 200

    deleted = await client.request(
        "DELETE", "/me", json={"password": PASSWORD}, headers=owner_headers
    )
    assert deleted.status_code == 204

    assert (await client.get("/me", headers=owner_headers)).status_code == 401
    refresh = await client.post(
        "/auth/refresh", json={"refreshToken": owner_tokens["refreshToken"]}
    )
    assert refresh.status_code == 401

    async with SessionLocal() as session:
        assert await session.get(User, UUID(owner_tokens["user"]["id"])) is None


async def test_registration_can_be_closed(client, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "registration_enabled", False)
    response = await client.post(
        "/auth/register", json={"email": "owner@example.com", "password": PASSWORD}
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "registration_closed"


async def test_user_cap_is_enforced(client, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "max_users", 1)
    first = await client.post(
        "/auth/register", json={"email": "owner@example.com", "password": PASSWORD}
    )
    assert first.status_code == 201

    second = await client.post(
        "/auth/register", json={"email": "other@example.com", "password": PASSWORD}
    )
    assert second.status_code == 403
    assert second.json()["error"]["code"] == "registration_closed"


async def test_uploads_can_be_disabled(client, owner_headers, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "uploads_enabled", False)
    response = await client.post(
        "/upload",
        files={"file": ("receipt.png", b"not used", "image/png")},
        headers=owner_headers,
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "uploads_disabled"
