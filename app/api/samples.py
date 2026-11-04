import hashlib
import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.api.receipts import serialize_receipt
from app.config import get_settings
from app.core.errors import ApiError
from app.core.rate_limit import limiter
from app.db.models import Receipt, ReceiptFile
from app.schemas.receipt import Receipt as ReceiptOut
from app.schemas.receipt import Sample
from app.services import samples as sample_service
from app.services import sheets
from app.services.pipeline import add_audit

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/samples", response_model=list[Sample])
async def list_samples() -> list[Sample]:
    return sample_service.public_samples()


@router.post("/samples/{sampleId}/process", status_code=202, response_model=ReceiptOut)
@limiter.limit(get_settings().rate_limit_upload)
async def process_sample(
    request: Request,
    sampleId: str,
    session: AsyncSession = Depends(get_session),
) -> ReceiptOut:
    golden = sample_service.golden(sampleId)
    if golden is None:
        raise ApiError(404, "receipt_not_found", "That sample does not exist.")

    data, checks, score, status = golden
    settings = get_settings()
    content = sample_service.sample_image(sampleId)
    stored = data.model_dump(mode="json", by_alias=True)

    receipt = Receipt(
        file_sha256=hashlib.sha256(content).hexdigest(),
        filename=f"{sampleId}.png",
        file_type="png",
        status=status,
        stage="done",
        confidence=score,
        sample_id=sampleId,
        pinned=True,
        cached=True,
        original_data=stored,
        data=stored,
        checks=[check.model_dump() for check in checks],
    )
    session.add(receipt)
    await session.flush()

    file_row = ReceiptFile(
        receipt_id=receipt.id,
        content=content,
        content_type="image/png",
        size_bytes=len(content),
        expires_at=datetime.now(UTC) + timedelta(hours=settings.file_retention_hours),
    )
    session.add(file_row)
    add_audit(session, receipt.id, "uploaded", details={"sample": sampleId})
    add_audit(session, receipt.id, "extracted", details={"cached": True})
    add_audit(session, receipt.id, "validated", details={"confidence": score})
    if status == "auto_approved":
        add_audit(session, receipt.id, "approved", details={"automatic": True})
        await sheets.enqueue(session, receipt.id)
    await session.commit()
    if status == "auto_approved":
        try:
            await sheets.sync_pending(session, settings)
        except Exception:
            logger.exception("sheet sync failed after sample approval")

    return serialize_receipt(receipt, file_row, request)
