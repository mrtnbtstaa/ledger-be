from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, File, Request, UploadFile
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_session
from app.api.receipts import serialize_receipt
from app.config import get_settings
from app.core.errors import ApiError
from app.core.rate_limit import limiter
from app.db.models import Receipt, ReceiptFile, User
from app.repos import receipts as receipt_repo
from app.repos import usage as usage_repo
from app.schemas.receipt import Receipt as ReceiptOut
from app.services import ingest, retention
from app.services.pipeline import add_audit, fail_stuck, process_receipt

router = APIRouter()


@router.post("/upload", status_code=202, response_model=ReceiptOut)
@limiter.limit(get_settings().rate_limit_upload)
async def upload_receipt(
    request: Request,
    background: BackgroundTasks,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ReceiptOut:
    settings = get_settings()
    if not settings.uploads_enabled:
        raise ApiError(
            503,
            "uploads_disabled",
            "Uploads are paused while accounts are being updated. Try a sample instead.",
        )

    content = await file.read(settings.max_file_bytes + 1)
    prepared = ingest.prepare(content)
    await usage_repo.consume_upload(
        session, user.id, datetime.now(UTC).date(), settings.user_daily_upload_limit
    )

    await fail_stuck(session)
    await retention.purge(session, settings)

    existing = await receipt_repo.find_by_hash(session, user.id, prepared.sha256)
    if existing is not None:
        existing.cached = True
        await session.commit()
        file_row = await receipt_repo.get_file(session, existing.id)
        return serialize_receipt(existing, file_row, request)

    receipt = Receipt(
        user_id=user.id,
        is_sample=False,
        file_sha256=prepared.sha256,
        filename=file.filename or "receipt",
        file_type=prepared.file_type,
        status="processing",
        stage="uploading",
    )
    session.add(receipt)
    await session.flush()

    file_row = ReceiptFile(
        receipt_id=receipt.id,
        content=prepared.content,
        content_type=prepared.content_type,
        size_bytes=len(prepared.content),
        expires_at=datetime.now(UTC) + timedelta(hours=settings.file_retention_hours),
    )
    session.add(file_row)
    add_audit(
        session,
        receipt.id,
        "uploaded",
        details={"filename": receipt.filename, "bytes": len(prepared.content)},
    )
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = await receipt_repo.find_by_hash(session, user.id, prepared.sha256)
        if existing is None:
            raise
        existing.cached = True
        await session.commit()
        file_row = await receipt_repo.get_file(session, existing.id)
        return serialize_receipt(existing, file_row, request)

    background.add_task(process_receipt, receipt.id)
    return serialize_receipt(receipt, file_row, request)
