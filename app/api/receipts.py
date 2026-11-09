import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_optional_user, get_session
from app.config import get_settings
from app.core.errors import ApiError
from app.core.rate_limit import limiter
from app.db.models import Receipt, ReceiptFile, User
from app.repos import receipts as receipt_repo
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
from app.services import deletion, review

logger = logging.getLogger(__name__)

router = APIRouter()


def _base_url(request: Request) -> str:
    configured = get_settings().public_base_url.strip()
    return configured.rstrip("/") if configured else str(request.base_url).rstrip("/")


def serialize_receipt(receipt: Receipt, file_row: ReceiptFile | None, request: Request) -> ReceiptOut:
    data = Extracted.model_validate(receipt.data) if receipt.data else None
    file_url = None
    file_expires_at = None
    if (
        not receipt.is_sample
        and receipt.status == "needs_review"
        and file_row is not None
        and file_row.expires_at > datetime.now(UTC)
    ):
        file_url = f"{_base_url(request)}/receipts/{receipt.id}/file"
        file_expires_at = file_row.expires_at

    return ReceiptOut(
        id=str(receipt.id),
        isSample=receipt.is_sample,
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
    file_row = await receipt_repo.get_file(session, receipt.id)
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
    ledger: bool = False,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ReceiptList:
    rows, total = await receipt_repo.list_owned(
        session,
        user.id,
        status=status.value if status is not None else None,
        category=category,
        q=q,
        sort=sort,
        order=order,
        limit=limit,
        offset=offset,
        ledger=ledger,
    )
    return ReceiptList(items=[serialize_summary(row) for row in rows], total=total)


@router.get("/receipts/{id}", response_model=ReceiptOut)
@limiter.limit(get_settings().rate_limit_read)
async def get_receipt(
    request: Request,
    id: UUID,
    user: User | None = Depends(get_optional_user),
    session: AsyncSession = Depends(get_session),
) -> ReceiptOut:
    receipt = await receipt_repo.get_visible(session, id, user.id if user is not None else None)
    return await _receipt_out(session, receipt, request)


@router.get("/receipts/{id}/file")
@limiter.limit(get_settings().rate_limit_read)
async def get_receipt_file(
    request: Request,
    id: UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    receipt = await receipt_repo.get_owned(session, user.id, id)
    file_row = await receipt_repo.get_file(session, receipt.id)
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
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ReceiptOut:
    receipt = await receipt_repo.get_owned(session, user.id, id)
    review.edit(session, receipt, to_domain(body.data))
    await session.commit()
    return await _receipt_out(session, receipt, request)


@router.post("/receipts/{id}/approve", response_model=ReceiptOut)
@limiter.limit(get_settings().rate_limit_review)
async def approve_receipt(
    request: Request,
    id: UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ReceiptOut:
    receipt = await receipt_repo.get_owned(session, user.id, id)
    review.approve(session, receipt)
    await receipt_repo.delete_file(session, receipt.id)
    await session.commit()
    return await _receipt_out(session, receipt, request)


@router.post("/receipts/{id}/reject", response_model=ReceiptOut)
@limiter.limit(get_settings().rate_limit_review)
async def reject_receipt(
    request: Request,
    id: UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ReceiptOut:
    receipt = await receipt_repo.get_owned(session, user.id, id)
    review.reject(session, receipt)
    await receipt_repo.delete_file(session, receipt.id)
    await session.commit()
    return await _receipt_out(session, receipt, request)


@router.delete("/receipts/{id}", status_code=204)
@limiter.limit(get_settings().rate_limit_review)
async def delete_receipt(
    request: Request,
    id: UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await deletion.delete_receipt(session, user.id, id)
    return Response(status_code=204)


@router.delete("/receipts", status_code=204)
@limiter.limit(get_settings().rate_limit_review)
async def delete_all_receipts(
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await deletion.delete_all(session, user.id)
    return Response(status_code=204)
