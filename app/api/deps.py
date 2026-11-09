from uuid import UUID

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core import tokens
from app.core.errors import ApiError
from app.db.models import User
from app.db.session import get_session
from app.repos import users as user_repo

__all__ = ["get_current_user", "get_optional_user", "get_session"]


def _unauthorized() -> ApiError:
    return ApiError(401, "unauthenticated", "Sign in to continue.")


async def _user_from_header(
    authorization: str | None, session: AsyncSession
) -> User | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise _unauthorized()
    payload = tokens.decode_access_token(token, get_settings())
    subject = payload.get("sub")
    if not subject:
        raise _unauthorized()
    try:
        user_id = UUID(str(subject))
    except ValueError as exc:
        raise _unauthorized() from exc
    user = await user_repo.get_by_id(session, user_id)
    if user is None:
        raise _unauthorized()
    return user


async def get_current_user(
    authorization: str | None = Header(None),
    session: AsyncSession = Depends(get_session),
) -> User:
    user = await _user_from_header(authorization, session)
    if user is None:
        raise _unauthorized()
    return user


async def get_optional_user(
    authorization: str | None = Header(None),
    session: AsyncSession = Depends(get_session),
) -> User | None:
    return await _user_from_header(authorization, session)
