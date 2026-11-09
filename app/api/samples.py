import hashlib
import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.api.receipts import serialize_receipt
from app.config import get_settings
from app.core.errors import ApiError
from app.core.rate_limit import limiter
from app.db.models import Receipt
from app.repos import receipts as receipt_repo
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

    receipt = await receipt_repo.get_sample_by_sample_id(session, sampleId)
    if receipt is None:
        receipt = Receipt(
            user_id=None,
            is_sample=True,
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
        add_audit(session, receipt.id, "uploaded", details={"sample": sampleId})
        add_audit(session, receipt.id, "extracted", details={"cached": True})
        add_audit(session, receipt.id, "validated", details={"confidence": score})
        if status == "auto_approved":
            add_audit(session, receipt.id, "approved", details={"automatic": True})
        await session.commit()
    else:
        receipt.cached = True
        await session.commit()

    if receipt.status == "auto_approved":
        await sheets.enqueue(session, sampleId)
        await session.commit()
        try:
            await sheets.sync_pending(session, settings)
        except Exception:
            logger.exception("sheet sync failed after sample approval")

    return serialize_receipt(receipt, None, request)
