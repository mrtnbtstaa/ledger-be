from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_session
from app.config import get_settings
from app.core.rate_limit import limiter
from app.db.models import User
from app.repos import receipts as receipt_repo
from app.services import export

router = APIRouter(tags=["receipts"])


@router.get("/receipts/export")
@limiter.limit(get_settings().rate_limit_read)
async def export_receipts(
    request: Request,
    format: str = Query("csv", pattern="^(csv|xlsx)$"),
    ledger: bool = False,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    rows = await receipt_repo.export_rows(session, user.id, ledger=ledger)
    if format == "xlsx":
        content = export.to_xlsx(rows)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = "ledger.xlsx"
    else:
        content = export.to_csv(rows)
        media_type = "text/csv; charset=utf-8"
        filename = "ledger.csv"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
