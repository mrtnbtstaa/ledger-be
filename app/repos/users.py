from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User


async def get_by_id(session: AsyncSession, user_id: UUID) -> User | None:
    return await session.get(User, user_id)


async def get_by_email(session: AsyncSession, email: str) -> User | None:
    return await session.scalar(select(User).where(User.email == email))


async def count(session: AsyncSession) -> int:
    return await session.scalar(select(func.count()).select_from(User)) or 0


def add(session: AsyncSession, user: User) -> None:
    session.add(user)


async def delete(session: AsyncSession, user: User) -> None:
    await session.delete(user)
