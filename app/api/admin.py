from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.config import get_settings
from app.core.errors import ApiError
from app.schemas.receipt import MaintenanceResult
from app.services import retention, sheets
from app.services.pipeline import fail_stuck

router = APIRouter()


@router.post("/admin/maintenance", response_model=MaintenanceResult)
async def maintenance(
    authorization: str | None = Header(None),
    session: AsyncSession = Depends(get_session),
) -> MaintenanceResult:
    settings = get_settings()
    if not settings.admin_token:
        raise ApiError(503, "maintenance_disabled", "Maintenance is not configured.")
    if authorization != f"Bearer {settings.admin_token}":
        raise ApiError(401, "unauthorized", "Admin token required.")

    purged_files, purged_records, purged_tokens = await retention.purge(session, settings)
    failed_stuck = await fail_stuck(session)
    sheet_synced = await sheets.sync_pending(session, settings)
    return MaintenanceResult(
        purgedFiles=purged_files,
        purgedRecords=purged_records,
        purgedTokens=purged_tokens,
        failedStuck=failed_stuck,
        sheetSynced=sheet_synced,
    )
