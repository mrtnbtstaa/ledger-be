from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
from sqlalchemy import func, insert, select

from app.config import get_settings
from app.db.models import Receipt
from app.db.session import engine
from app.services import export


def _payload(user_id: str) -> dict:
    settings = get_settings()
    now = datetime.now(UTC)
    return {
        "sub": user_id,
        "iat": now,
        "exp": now + timedelta(minutes=15),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    }


async def test_token_attacks_are_rejected(client, owner_tokens) -> None:
    settings = get_settings()
    user_id = owner_tokens["user"]["id"]
    payload = _payload(user_id)

    unsigned = jwt.encode(payload, None, algorithm="none")
    no_subject = jwt.encode(
        {key: value for key, value in payload.items() if key != "sub"},
        settings.jwt_secret,
        algorithm="HS256",
    )
    tampered = owner_tokens["accessToken"][:-4] + "AAAA"

    for token in (unsigned, no_subject, tampered):
        response = await client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401, token
        assert response.json()["error"]["code"] == "unauthenticated"


async def test_previous_secret_is_accepted_during_rotation(client, owner_tokens, monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "jwt_secret_previous", "previous-secret-0123456789abcdef")

    token = jwt.encode(
        _payload(owner_tokens["user"]["id"]),
        settings.jwt_secret_previous,
        algorithm="HS256",
    )
    response = await client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["email"] == "owner@example.com"


def test_export_escapes_formula_cells() -> None:
    assert export.escape_cell("=SUM(A1)") == "'=SUM(A1)"
    assert export.escape_cell("+1") == "'+1"
    assert export.escape_cell("-1") == "'-1"
    assert export.escape_cell("@cmd") == "'@cmd"
    assert export.escape_cell("plain") == "plain"
    assert export.escape_cell(None) == ""


async def test_export_contains_only_owner_rows(client, owner_tokens, other_tokens, owner_headers) -> None:
    async with engine.begin() as connection:
        for user_id, filename in (
            (owner_tokens["user"]["id"], "owner-row.png"),
            (other_tokens["user"]["id"], "other-row.png"),
        ):
            await connection.execute(
                insert(Receipt).values(
                    user_id=UUID(user_id),
                    is_sample=False,
                    file_sha256=uuid4().hex + uuid4().hex,
                    filename=filename,
                    file_type="png",
                    status="auto_approved",
                    data={"vendor": filename, "category": "other"},
                )
            )

    response = await client.get("/receipts/export", headers=owner_headers)
    assert response.status_code == 200
    body = response.text
    assert "owner-row.png" in body
    assert "other-row.png" not in body


async def test_validate_is_public_and_stateless(client) -> None:
    response = await client.post(
        "/validate",
        json={
            "data": {
                "vendor": "Blue Bottle Coffee",
                "date": "2026-11-01",
                "dateRaw": "11/01/2026",
                "currency": "USD",
                "subtotal": 10,
                "tax": 1,
                "total": 11,
                "category": "dining",
                "lineItems": [],
            }
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["confidence"] > 0
    assert any(check["rule"] == "required_fields_present" for check in body["checks"])

    async with engine.connect() as connection:
        count = await connection.scalar(
            select(func.count()).select_from(Receipt).where(Receipt.is_sample.is_(False))
        )
    assert count == 0
