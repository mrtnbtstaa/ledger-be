import hashlib
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RefreshToken


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def get_by_hash(session: AsyncSession, token_hash: str) -> RefreshToken | None:
    return await session.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))


async def revoke_family(
    session: AsyncSession, family_id: UUID, now: datetime | None = None
) -> None:
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now or datetime.now(UTC))
    )


async def revoke_all_for_user(session: AsyncSession, user_id: UUID) -> None:
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
