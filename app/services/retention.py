from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import Receipt, ReceiptFile


async def purge(session: AsyncSession, settings: Settings) -> tuple[int, int]:
    now = datetime.now(UTC)
    pinned = select(Receipt.id).where(Receipt.pinned.is_(True))
    files = await session.execute(
        delete(ReceiptFile).where(
            ReceiptFile.expires_at <= now,
            ReceiptFile.receipt_id.not_in(pinned),
        )
    )
    records = await session.execute(
        delete(Receipt).where(
            Receipt.uploaded_at <= now - timedelta(days=settings.record_retention_days),
            Receipt.pinned.is_(False),
        )
    )
    await session.commit()
    return files.rowcount or 0, records.rowcount or 0  # type: ignore[attr-defined]
