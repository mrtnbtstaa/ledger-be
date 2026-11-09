from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_session
from app.config import get_settings
from app.db.models import User
from app.schemas.auth import DeleteAccountRequest, UsageOut, UserOut
from app.services import accounts

router = APIRouter(tags=["me"])


def _out(user: User) -> UserOut:
    return UserOut(id=str(user.id), email=user.email, createdAt=user.created_at)


@router.get("/me", response_model=UserOut)
async def get_me(user: User = Depends(get_current_user)) -> UserOut:
    return _out(user)


@router.get("/me/usage", response_model=UsageOut)
async def get_usage(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> UsageOut:
    usage = await accounts.usage(session, user, get_settings())
    day = datetime.now(UTC).date()
    return UsageOut(
        day=day.isoformat(),
        uploads=usage.uploads if usage is not None else 0,
        limit=get_settings().user_daily_upload_limit,
    )


@router.delete("/me", status_code=204)
async def delete_me(
    body: DeleteAccountRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await accounts.delete_account(session, user, body.password)
    return Response(status_code=204)
