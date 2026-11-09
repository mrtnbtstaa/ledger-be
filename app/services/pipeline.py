import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import Receipt, ReceiptFile
from app.db.session import SessionLocal
from app.repos import audit as audit_repo
from app.repos import receipts as receipt_repo
from app.services import confidence, pdf_text, validation
from app.services.extraction import ExtractionFailed, extract_receipt
from app.services.llm.client import LLMQuotaExhausted, LLMUnavailable

logger = logging.getLogger(__name__)


def add_audit(
    session: AsyncSession,
    receipt_id: UUID,
    event_type: str,
    actor: str = "system",
    details: dict[str, Any] | None = None,
) -> None:
    audit_repo.add(session, receipt_id, event_type, actor=actor, details=details)


async def _alive(session: AsyncSession, receipt_id: UUID) -> bool:
    return await session.scalar(select(Receipt.id).where(Receipt.id == receipt_id)) is not None


async def fail_stuck(session: AsyncSession) -> int:
    settings = get_settings()
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.processing_timeout_seconds)
    rows = (
        (
            await session.execute(
                select(Receipt).where(Receipt.status == "processing", Receipt.updated_at < cutoff)
            )
        )
        .scalars()
        .all()
    )
    for receipt in rows:
        receipt.status = "failed"
        receipt.stage = "done"
        receipt.failure_reason = "Processing was interrupted. Please try again."
        add_audit(session, receipt.id, "failed", details={"reason": receipt.failure_reason})
        await receipt_repo.delete_file(session, receipt.id)
    if rows:
        await session.commit()
    return len(rows)


async def _fail(session: AsyncSession, receipt_id: UUID, reason: str) -> None:
    if not await _alive(session, receipt_id):
        await session.rollback()
        return
    receipt = await session.get(Receipt, receipt_id)
    if receipt is None:
        await session.rollback()
        return
    receipt.status = "failed"
    receipt.stage = "done"
    receipt.failure_reason = reason
    add_audit(session, receipt_id, "failed", details={"reason": reason})
    await receipt_repo.delete_file(session, receipt_id)
    await session.commit()


async def process_receipt(receipt_id: UUID) -> None:
    settings = get_settings()
    async with SessionLocal() as session:
        receipt = await session.get(Receipt, receipt_id)
        if receipt is None or receipt.status != "processing":
            return
        file_type = receipt.file_type

        try:
            receipt.stage = "extracting"
            await session.commit()

            file_row = await session.get(ReceiptFile, receipt_id)
            if file_row is None:
                raise ExtractionFailed("The uploaded file is no longer available.")

            source_text = None
            if file_type == "pdf":
                text = pdf_text.extract_text(file_row.content)
                source_text = text if len(text.strip()) >= 40 else None

            result = await extract_receipt(
                receipt_id=receipt_id,
                content=file_row.content,
                file_type=file_type,
                source_text=source_text,
                session=session,
                settings=settings,
            )

            if not await _alive(session, receipt_id):
                await session.rollback()
                return

            receipt.original_data = result.data.model_dump(mode="json", by_alias=True)
            receipt.data = receipt.original_data
            add_audit(session, receipt_id, "extracted", details={"model": "groq"})

            receipt.stage = "validating"
            await session.commit()

            if not await _alive(session, receipt_id):
                await session.rollback()
                return

            checks = validation.run_checks(result.data, result.source_text)
            score = confidence.compute_confidence(checks, result.data, result.source_text is not None)
            status = confidence.route(checks, score, settings.auto_approve_threshold)

            receipt.checks = [check.model_dump() for check in checks]
            receipt.confidence = score
            receipt.status = status
            receipt.stage = "done"
            add_audit(
                session,
                receipt_id,
                "validated",
                details={
                    "confidence": score,
                    "failed": [c.rule for c in checks if not c.passed and not c.skipped],
                },
            )
            if status == "auto_approved":
                add_audit(session, receipt_id, "approved", details={"automatic": True})
            if status in ("auto_approved", "failed"):
                await receipt_repo.delete_file(session, receipt_id)
            await session.commit()
        except LLMQuotaExhausted:
            await _fail(
                session, receipt_id, "The demo's daily extraction budget is used up. Try a sample receipt."
            )
        except LLMUnavailable:
            await _fail(
                session,
                receipt_id,
                "The extraction service is unavailable. Try again or use a sample receipt.",
            )
        except ExtractionFailed as exc:
            await _fail(session, receipt_id, str(exc))
        except Exception:
            logger.exception("receipt processing failed")
            await _fail(session, receipt_id, "The receipt could not be processed.")
