from datetime import UTC, datetime, timedelta
from decimal import Decimal
from io import BytesIO
from uuid import UUID, uuid4

import jwt
from PIL import Image
from sqlalchemy import insert

from app.config import get_settings
from app.db.models import Receipt, ReceiptFile, User
from app.db.session import SessionLocal, engine
from app.schemas.extraction import Extracted
from app.services import pipeline
from app.services.extraction import ExtractionResult


def png_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (20, 20), "white").save(output, format="PNG")
    return output.getvalue()


async def insert_owned_receipt(owner_id: str, filename: str, status: str) -> UUID:
    receipt_id = uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            insert(Receipt).values(
                id=receipt_id,
                user_id=UUID(owner_id),
                is_sample=False,
                file_sha256=uuid4().hex + uuid4().hex,
                filename=filename,
                file_type="png",
                status=status,
                data={"vendor": filename, "category": "other"},
            )
        )
        await connection.execute(
            insert(ReceiptFile).values(
                receipt_id=receipt_id,
                content=b"secret bytes",
                content_type="image/png",
                size_bytes=12,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
    return receipt_id


async def test_foreign_receipt_is_invisible(client, owner_tokens, other_headers) -> None:
    receipt_id = await insert_owned_receipt(
        owner_tokens["user"]["id"], "owner.png", "needs_review"
    )

    detail = await client.get(f"/receipts/{receipt_id}", headers=other_headers)
    assert detail.status_code == 404
    assert detail.json()["error"]["code"] == "receipt_not_found"

    edit = await client.patch(
        f"/receipts/{receipt_id}",
        json={
            "data": {
                "vendor": "Hijacked",
                "date": "2026-11-01",
                "dateRaw": "11/01/2026",
                "currency": "USD",
                "subtotal": 10,
                "tax": 1,
                "total": 11,
                "category": "other",
                "lineItems": [],
            }
        },
        headers=other_headers,
    )
    assert edit.status_code == 404

    for action in ("approve", "reject"):
        response = await client.post(f"/receipts/{receipt_id}/{action}", headers=other_headers)
        assert response.status_code == 404

    file_response = await client.get(f"/receipts/{receipt_id}/file", headers=other_headers)
    assert file_response.status_code == 404

    deleted = await client.delete(f"/receipts/{receipt_id}", headers=other_headers)
    assert deleted.status_code == 404

    async with SessionLocal() as session:
        assert await session.get(Receipt, receipt_id) is not None


async def test_lists_are_owner_scoped(client, owner_tokens, other_tokens, other_headers) -> None:
    await insert_owned_receipt(owner_tokens["user"]["id"], "owner.png", "auto_approved")
    await insert_owned_receipt(other_tokens["user"]["id"], "other.png", "auto_approved")

    owner_list = await client.get(
        "/receipts", headers={"Authorization": f"Bearer {owner_tokens['accessToken']}"}
    )
    assert owner_list.json()["total"] == 1
    assert [item["filename"] for item in owner_list.json()["items"]] == ["owner.png"]

    other_list = await client.get("/receipts", headers=other_headers)
    assert other_list.json()["total"] == 1
    assert [item["filename"] for item in other_list.json()["items"]] == ["other.png"]


async def test_anonymous_cannot_read_user_receipts(client, owner_tokens) -> None:
    receipt_id = await insert_owned_receipt(
        owner_tokens["user"]["id"], "owner.png", "needs_review"
    )

    for path in ("/receipts", f"/receipts/{receipt_id}", f"/receipts/{receipt_id}/file"):
        response = await client.get(path)
        assert response.status_code == 401, path
        assert response.json()["error"]["code"] == "unauthenticated"


async def test_invalid_tokens_are_rejected(client, owner_tokens) -> None:
    settings = get_settings()
    user_id = owner_tokens["user"]["id"]
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + timedelta(minutes=15),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    }

    expired = jwt.encode(
        {**payload, "exp": now - timedelta(minutes=1)}, settings.jwt_secret, algorithm="HS256"
    )
    wrong_key = jwt.encode(
        payload, "wrong-secret-0123456789abcdef0123456789abcdef", algorithm="HS256"
    )
    wrong_issuer = jwt.encode(
        {**payload, "iss": "someone-else"}, settings.jwt_secret, algorithm="HS256"
    )
    wrong_audience = jwt.encode(
        {**payload, "aud": "someone-else"}, settings.jwt_secret, algorithm="HS256"
    )

    for token in (expired, wrong_key, wrong_issuer, wrong_audience, "not-a-token"):
        response = await client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401, token

    async with SessionLocal() as session:
        user = await session.get(User, UUID(user_id))
        assert user is not None
        await session.delete(user)
        await session.commit()

    deleted_user = await client.get(
        "/me", headers={"Authorization": f"Bearer {owner_tokens['accessToken']}"}
    )
    assert deleted_user.status_code == 401


async def test_identical_upload_is_independent_per_user(
    client, owner_tokens, other_tokens, monkeypatch
) -> None:
    async def fake_extract(**kwargs):
        return ExtractionResult(
            data=Extracted(
                vendor="Same Vendor",
                date="2026-11-01",
                dateRaw="11/01/2026",
                currency="USD",
                subtotal=Decimal("10.00"),
                tax=Decimal("1.00"),
                total=Decimal("11.00"),
                category="other",
                lineItems=[],
            ),
            source_text=None,
        )

    monkeypatch.setattr(pipeline, "extract_receipt", fake_extract)
    content = png_bytes()

    first = await client.post(
        "/upload",
        files={"file": ("receipt.png", content, "image/png")},
        headers={"Authorization": f"Bearer {owner_tokens['accessToken']}"},
    )
    second = await client.post(
        "/upload",
        files={"file": ("receipt.png", content, "image/png")},
        headers={"Authorization": f"Bearer {other_tokens['accessToken']}"},
    )

    assert first.status_code == second.status_code == 202
    assert first.json()["id"] != second.json()["id"]
    assert second.json()["cached"] is False


async def test_samples_never_appear_in_user_lists(client, owner_headers) -> None:
    sample = (await client.post("/samples/blue-bottle/process")).json()
    listing = await client.get("/receipts", headers=owner_headers)
    assert listing.json()["total"] == 0
    assert sample["id"] not in [item["id"] for item in listing.json()["items"]]
