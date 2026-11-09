from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import delete
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import Receipt, ReceiptFile, RefreshToken


async def purge(session: AsyncSession, settings: Settings) -> tuple[int, int, int]:
    now = datetime.now(UTC)
    files = cast(
        CursorResult[Any],
        await session.execute(delete(ReceiptFile).where(ReceiptFile.expires_at <= now)),
    )
    records = cast(
        CursorResult[Any],
        await session.execute(
            delete(Receipt).where(
                Receipt.uploaded_at <= now - timedelta(days=settings.record_retention_days),
                Receipt.status.in_(("failed", "rejected", "needs_review")),
                Receipt.is_sample.is_(False),
            )
        ),
    )
    tokens = cast(
        CursorResult[Any],
        await session.execute(
            delete(RefreshToken).where(
                (RefreshToken.expires_at <= now)
                | (
                    RefreshToken.revoked_at.is_not(None)
                    & (
                        RefreshToken.revoked_at
                        <= now - timedelta(seconds=settings.refresh_reuse_grace_seconds)
                    )
                )
            )
        ),
    )
    await session.commit()
    return (
        files.rowcount or 0,
        records.rowcount or 0,
        tokens.rowcount or 0,
    )
