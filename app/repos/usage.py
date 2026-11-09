from datetime import date
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.db.models import UserUsage


async def consume_upload(
    session: AsyncSession, user_id: UUID, day: date, limit: int
) -> int:
    statement = (
        insert(UserUsage)
        .values(user_id=user_id, day=day, uploads=1)
        .on_conflict_do_update(
            index_elements=[UserUsage.user_id, UserUsage.day],
            set_={"uploads": UserUsage.uploads + 1},
            where=UserUsage.uploads < limit,
        )
        .returning(UserUsage.uploads)
    )
    uploads = await session.scalar(statement)
    if uploads is None:
        raise ApiError(
            429,
            "rate_limited",
            "You have reached today's upload limit. Try again tomorrow.",
            3600,
        )
    return uploads


async def get(session: AsyncSession, user_id: UUID, day: date) -> UserUsage | None:
    return await session.scalar(
        select(UserUsage).where(UserUsage.user_id == user_id, UserUsage.day == day)
    )


async def delete_for_user(session: AsyncSession, user_id: UUID) -> None:
    await session.execute(delete(UserUsage).where(UserUsage.user_id == user_id))
