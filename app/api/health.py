from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.config import get_settings
from app.schemas.receipt import Health

router = APIRouter()


@router.get("/health", response_model=Health)
async def health(session: AsyncSession = Depends(get_session)) -> Health:
    settings = get_settings()
    db = "ok"
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        db = "unavailable"
    return Health(
        status="ok",
        version=settings.app_version,
        time=datetime.now(UTC),
        db=db,
    )
