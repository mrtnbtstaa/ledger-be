from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, File, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.api.receipts import serialize_receipt
from app.config import get_settings
from app.core.rate_limit import limiter
from app.db.models import Receipt, ReceiptFile
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
    session: AsyncSession = Depends(get_session),
) -> ReceiptOut:
    settings = get_settings()
    content = await file.read(settings.max_file_bytes + 1)
    prepared = ingest.prepare(content)

    await fail_stuck(session)
    await retention.purge(session, settings)

    existing = (
        await session.execute(
            select(Receipt).where(
                Receipt.file_sha256 == prepared.sha256,
                Receipt.sample_id.is_(None),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.cached = True
        await session.commit()
        file_row = await session.get(ReceiptFile, existing.id)
        return serialize_receipt(existing, file_row, request)

    receipt = Receipt(
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
    await session.commit()

    background.add_task(process_receipt, receipt.id)
    return serialize_receipt(receipt, file_row, request)
