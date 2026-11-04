import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import Numeric, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session, require_passcode
from app.config import get_settings
from app.core.errors import ApiError
from app.core.rate_limit import limiter
from app.db.models import Receipt, ReceiptFile
from app.schemas.extraction import Extracted
from app.schemas.receipt import (
    Receipt as ReceiptOut,
)
from app.schemas.receipt import (
    ReceiptList,
    ReceiptStatus,
    ReceiptSummary,
    ReceiptUpdate,
    to_checks_out,
    to_domain,
    to_extracted_out,
)
from app.services import review, sheets

logger = logging.getLogger(__name__)

router = APIRouter()


def _base_url(request: Request) -> str:
    configured = get_settings().public_base_url.strip()
    return configured.rstrip("/") if configured else str(request.base_url).rstrip("/")


def serialize_receipt(receipt: Receipt, file_row: ReceiptFile | None, request: Request) -> ReceiptOut:
    data = Extracted.model_validate(receipt.data) if receipt.data else None
    file_url = None
    file_expires_at = None
    if file_row is not None and file_row.expires_at > datetime.now(UTC):
        file_url = f"{_base_url(request)}/receipts/{receipt.id}/file"
        file_expires_at = file_row.expires_at

    return ReceiptOut(
        id=str(receipt.id),
        filename=receipt.filename,
        fileType=receipt.file_type,
        uploadedAt=receipt.uploaded_at,
        status=receipt.status,
        stage=receipt.stage,
        confidence=float(receipt.confidence) if receipt.confidence is not None else None,
        failureReason=receipt.failure_reason,
        edited=receipt.edited,
        cached=receipt.cached,
        fileUrl=file_url,
        fileExpiresAt=file_expires_at,
        data=to_extracted_out(data) if data is not None else None,
        checks=to_checks_out(receipt.checks or []),
    )


async def _receipt_out(session: AsyncSession, receipt: Receipt, request: Request) -> ReceiptOut:
    file_row = await session.get(ReceiptFile, receipt.id)
    return serialize_receipt(receipt, file_row, request)


def _summary_reason(receipt: Receipt, checks: list[dict[str, Any]]) -> str | None:
    if receipt.failure_reason:
        return receipt.failure_reason
    failing = next(
        (check for check in checks if not check.get("passed") and not check.get("skipped")),
        None,
    )
    if failing is not None:
        return failing.get("details") or failing.get("description")
    if receipt.status == "needs_review" and receipt.confidence is not None:
        threshold = get_settings().auto_approve_threshold
        score = float(receipt.confidence)
        if score < threshold:
            return f"Confidence is {score:.2f}, below the {threshold:.2f} review threshold."
    return None


def serialize_summary(receipt: Receipt) -> ReceiptSummary:
    data = Extracted.model_validate(receipt.data) if receipt.data else None
    return ReceiptSummary(
        id=str(receipt.id),
        filename=receipt.filename,
        uploadedAt=receipt.uploaded_at,
        vendor=data.vendor if data else None,
        date=data.date if data else None,
        total=float(data.total) if data and data.total is not None else None,
        currency=data.currency if data else None,
        category=data.category if data else None,
        status=receipt.status,
        confidence=float(receipt.confidence) if receipt.confidence is not None else None,
        failureReason=_summary_reason(receipt, receipt.checks or []),
    )


async def _load_receipt(session: AsyncSession, receipt_id: UUID) -> Receipt:
    receipt = await session.get(Receipt, receipt_id)
    if receipt is None:
        raise ApiError(404, "receipt_not_found", "That receipt does not exist.")
    return receipt


@router.get("/receipts", response_model=ReceiptList)
@limiter.limit(get_settings().rate_limit_read)
async def list_receipts(
    request: Request,
    status: ReceiptStatus | None = None,
    category: str | None = None,
    q: str | None = Query(None, max_length=100),
    sort: str = Query("date", pattern="^(date|total)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> ReceiptList:
    conditions: list[Any] = []
    if status is not None:
        conditions.append(Receipt.status == status.value)
    if category is not None:
        conditions.append(Receipt.data["category"].astext == category)
    if q is not None and q.strip():
        # shortcut: substring search scans; add a trigram index when the table grows.
        term = q.strip().lower()
        conditions.append(
            or_(
                func.lower(Receipt.filename).contains(term, autoescape=True),
                func.lower(Receipt.data["vendor"].astext).contains(term, autoescape=True),
            )
        )

    if sort == "total":
        total_expr = cast(Receipt.data["total"].astext, Numeric)
        order_by = total_expr.desc().nulls_last() if order == "desc" else total_expr.asc().nulls_first()
    else:
        order_by = Receipt.uploaded_at.desc() if order == "desc" else Receipt.uploaded_at.asc()

    total = await session.scalar(select(func.count()).select_from(Receipt).where(*conditions)) or 0
    rows = (
        (
            await session.execute(
                select(Receipt).where(*conditions).order_by(order_by).limit(limit).offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return ReceiptList(items=[serialize_summary(row) for row in rows], total=total)


@router.get("/receipts/{id}", response_model=ReceiptOut)
@limiter.limit(get_settings().rate_limit_read)
async def get_receipt(
    request: Request,
    id: UUID,
    session: AsyncSession = Depends(get_session),
) -> ReceiptOut:
    receipt = await _load_receipt(session, id)
    return await _receipt_out(session, receipt, request)


@router.get("/receipts/{id}/file")
@limiter.limit(get_settings().rate_limit_read)
async def get_receipt_file(
    request: Request,
    id: UUID,
    session: AsyncSession = Depends(get_session),
) -> Response:
    receipt = await _load_receipt(session, id)
    file_row = await session.get(ReceiptFile, id)
    if file_row is None or file_row.expires_at <= datetime.now(UTC):
        raise ApiError(404, "file_expired", "The original file is no longer available.")
    filename = receipt.filename.replace('"', "").replace("\n", "")
    return Response(
        content=file_row.content,
        media_type=file_row.content_type,
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.patch("/receipts/{id}", response_model=ReceiptOut)
@limiter.limit(get_settings().rate_limit_review)
async def update_receipt(
    request: Request,
    id: UUID,
    body: ReceiptUpdate,
    _: None = Depends(require_passcode),
    session: AsyncSession = Depends(get_session),
) -> ReceiptOut:
    receipt = await _load_receipt(session, id)
    review.edit(session, receipt, to_domain(body.data))
    await session.commit()
    return await _receipt_out(session, receipt, request)


@router.post("/receipts/{id}/approve", response_model=ReceiptOut)
@limiter.limit(get_settings().rate_limit_review)
async def approve_receipt(
    request: Request,
    id: UUID,
    _: None = Depends(require_passcode),
    session: AsyncSession = Depends(get_session),
) -> ReceiptOut:
    receipt = await _load_receipt(session, id)
    review.approve(session, receipt)
    await sheets.enqueue(session, receipt.id)
    await session.commit()
    try:
        await sheets.sync_pending(session, get_settings())
    except Exception:
        logger.exception("sheet sync failed after approval")
    return await _receipt_out(session, receipt, request)


@router.post("/receipts/{id}/reject", response_model=ReceiptOut)
@limiter.limit(get_settings().rate_limit_review)
async def reject_receipt(
    request: Request,
    id: UUID,
    _: None = Depends(require_passcode),
    session: AsyncSession = Depends(get_session),
) -> ReceiptOut:
    receipt = await _load_receipt(session, id)
    review.reject(session, receipt)
    await session.commit()
    return await _receipt_out(session, receipt, request)
