from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.repos import receipts as receipt_repo
from app.repos import usage as usage_repo


async def delete_receipt(session: AsyncSession, owner_id: UUID, receipt_id: UUID) -> None:
    await receipt_repo.delete_owned(session, owner_id, receipt_id)
    await session.commit()


async def delete_all(session: AsyncSession, owner_id: UUID) -> int:
    count = await receipt_repo.delete_all_owned(session, owner_id)
    await usage_repo.delete_for_user(session, owner_id)
    await session.commit()
    return count
