from datetime import UTC, datetime, timedelta

from app.config import get_settings
from app.db.models import Receipt, ReceiptFile
from app.db.session import SessionLocal
from app.services import retention


async def test_purge_removes_expired_files_and_old_unpinned_records() -> None:
    now = datetime.now(UTC)
    async with SessionLocal() as session:
        old = Receipt(
            file_sha256="a" * 64,
            filename="old.png",
            file_type="png",
            uploaded_at=now - timedelta(days=8),
            status="approved",
            stage="done",
        )
        pinned = Receipt(
            file_sha256="b" * 64,
            filename="sample.png",
            file_type="png",
            uploaded_at=now - timedelta(days=8),
            status="auto_approved",
            stage="done",
            pinned=True,
        )
        session.add_all([old, pinned])
        await session.flush()
        session.add_all(
            [
                ReceiptFile(
                    receipt_id=old.id,
                    content=b"x",
                    content_type="image/png",
                    size_bytes=1,
                    expires_at=now - timedelta(hours=1),
                ),
                ReceiptFile(
                    receipt_id=pinned.id,
                    content=b"y",
                    content_type="image/png",
                    size_bytes=1,
                    expires_at=now - timedelta(hours=1),
                ),
            ]
        )
        await session.commit()
        old_id, pinned_id = old.id, pinned.id

    async with SessionLocal() as session:
        purged_files, purged_records = await retention.purge(session, get_settings())

    assert purged_files == 1
    assert purged_records == 1

    async with SessionLocal() as session:
        assert await session.get(Receipt, old_id) is None
        assert await session.get(ReceiptFile, old_id) is None
        assert await session.get(Receipt, pinned_id) is not None
        assert await session.get(ReceiptFile, pinned_id) is not None


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
    assert {"purgedFiles", "purgedRecords", "failedStuck"} <= allowed.json().keys()
