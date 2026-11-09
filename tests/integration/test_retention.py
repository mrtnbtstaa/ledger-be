from datetime import UTC, datetime, timedelta

from app.config import get_settings
from app.db.models import Receipt, ReceiptFile, RefreshToken, User
from app.db.session import SessionLocal
from app.services import retention


async def test_purge_removes_expired_files_and_old_non_ledger_records() -> None:
    now = datetime.now(UTC)
    async with SessionLocal() as session:
        user = User(email="retention@example.com", password_hash="x")
        session.add(user)
        await session.flush()

        old_review = Receipt(
            user_id=user.id,
            is_sample=False,
            file_sha256="a" * 64,
            filename="old.png",
            file_type="png",
            uploaded_at=now - timedelta(days=8),
            status="needs_review",
            stage="done",
        )
        old_ledger = Receipt(
            user_id=user.id,
            is_sample=False,
            file_sha256="b" * 64,
            filename="ledger.png",
            file_type="png",
            uploaded_at=now - timedelta(days=8),
            status="approved",
            stage="done",
        )
        pinned_sample = Receipt(
            user_id=None,
            is_sample=True,
            file_sha256="c" * 64,
            filename="sample.png",
            file_type="png",
            uploaded_at=now - timedelta(days=8),
            status="auto_approved",
            stage="done",
            pinned=True,
        )
        session.add_all([old_review, old_ledger, pinned_sample])
        await session.flush()
        session.add_all(
            [
                ReceiptFile(
                    receipt_id=receipt.id,
                    content=b"x",
                    content_type="image/png",
                    size_bytes=1,
                    expires_at=now - timedelta(hours=1),
                )
                for receipt in (old_review, old_ledger, pinned_sample)
            ]
        )
        session.add_all(
            [
                RefreshToken(
                    user_id=user.id,
                    token_hash="d" * 64,
                    expires_at=now - timedelta(days=1),
                ),
                RefreshToken(
                    user_id=user.id,
                    token_hash="e" * 64,
                    expires_at=now + timedelta(days=1),
                    revoked_at=now - timedelta(minutes=5),
                ),
            ]
        )
        await session.commit()
        old_review_id = old_review.id
        old_ledger_id = old_ledger.id
        pinned_sample_id = pinned_sample.id

    async with SessionLocal() as session:
        purged_files, purged_records, purged_tokens = await retention.purge(
            session, get_settings()
        )

    assert purged_files == 3
    assert purged_records == 1
    assert purged_tokens == 2

    async with SessionLocal() as session:
        assert await session.get(Receipt, old_review_id) is None
        assert await session.get(ReceiptFile, old_review_id) is None
        assert await session.get(Receipt, old_ledger_id) is not None
        assert await session.get(Receipt, pinned_sample_id) is not None


async def test_admin_maintenance_is_disabled_without_a_token(client) -> None:
    response = await client.post("/admin/maintenance")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "maintenance_disabled"


async def test_admin_maintenance_requires_the_bearer_token(client, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "admin_token", "secret")

    denied = await client.post("/admin/maintenance")
    assert denied.status_code == 401
    assert denied.json()["error"]["code"] == "unauthorized"

    allowed = await client.post(
        "/admin/maintenance",
        headers={"Authorization": "Bearer secret"},
    )
    assert allowed.status_code == 200
    assert {"purgedFiles", "purgedRecords", "purgedTokens", "failedStuck"} <= allowed.json().keys()
