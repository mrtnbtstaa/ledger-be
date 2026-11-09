from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.config import get_settings
from app.core.rate_limit import limiter
from app.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
)
from app.services import accounts

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", status_code=201, response_model=TokenPair)
@limiter.limit(get_settings().rate_limit_register)
async def register(
    request: Request,
    body: RegisterRequest,
    session: AsyncSession = Depends(get_session),
) -> TokenPair:
    return await accounts.register(session, body.email, body.password, get_settings())


@router.post("/login", response_model=TokenPair)
@limiter.limit(get_settings().rate_limit_auth)
async def login(
    request: Request,
    body: LoginRequest,
    session: AsyncSession = Depends(get_session),
) -> TokenPair:
    return await accounts.login(session, body.email, body.password, get_settings())


@router.post("/refresh", response_model=TokenPair)
@limiter.limit(get_settings().rate_limit_auth)
async def refresh(
    request: Request,
    body: RefreshRequest,
    session: AsyncSession = Depends(get_session),
) -> TokenPair:
    return await accounts.refresh(session, body.refresh_token, get_settings())


@router.post("/logout", status_code=204)
@limiter.limit(get_settings().rate_limit_auth)
async def logout(
    request: Request,
    body: LogoutRequest,
    session: AsyncSession = Depends(get_session),
) -> Response:
    await accounts.logout(session, body.refresh_token)
    return Response(status_code=204)
