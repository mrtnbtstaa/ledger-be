import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import AuditEvent, Receipt, ReceiptFile
from app.db.session import SessionLocal
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
    session.add(AuditEvent(receipt_id=receipt_id, type=event_type, actor=actor, details=details or {}))


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
    if rows:
        await session.commit()
    return len(rows)


async def _fail(session: AsyncSession, receipt: Receipt, reason: str) -> None:
    receipt.status = "failed"
    receipt.stage = "done"
    receipt.failure_reason = reason
    add_audit(session, receipt.id, "failed", details={"reason": reason})
    await session.commit()


async def process_receipt(receipt_id: UUID) -> None:
    settings = get_settings()
    async with SessionLocal() as session:
        receipt = await session.get(Receipt, receipt_id)
        if receipt is None or receipt.status != "processing":
            return

        try:
            receipt.stage = "extracting"
            await session.commit()

            file_row = await session.get(ReceiptFile, receipt_id)
            if file_row is None:
                raise ExtractionFailed("The uploaded file is no longer available.")

            source_text = None
            if receipt.file_type == "pdf":
                text = pdf_text.extract_text(file_row.content)
                source_text = text if len(text.strip()) >= 40 else None

            result = await extract_receipt(
                receipt_id=receipt.id,
                content=file_row.content,
                file_type=receipt.file_type,
                source_text=source_text,
                session=session,
                settings=settings,
            )

            receipt.original_data = result.data.model_dump(mode="json", by_alias=True)
            receipt.data = receipt.original_data
            add_audit(session, receipt.id, "extracted", details={"model": "groq"})

            receipt.stage = "validating"
            await session.commit()

            checks = validation.run_checks(result.data, result.source_text)
            score = confidence.compute_confidence(checks, result.data, result.source_text is not None)
            status = confidence.route(checks, score, settings.auto_approve_threshold)

            receipt.checks = [check.model_dump() for check in checks]
            receipt.confidence = score
            receipt.status = status
            receipt.stage = "done"
            add_audit(
                session,
                receipt.id,
                "validated",
                details={
                    "confidence": score,
                    "failed": [c.rule for c in checks if not c.passed and not c.skipped],
                },
            )
            if status == "auto_approved":
                add_audit(session, receipt.id, "approved", details={"automatic": True})
                from app.services import sheets

                await sheets.enqueue(session, receipt.id)
            await session.commit()
            if status == "auto_approved":
                try:
                    await sheets.sync_pending(session, settings)
                except Exception:
                    logger.exception("sheet sync failed after auto approval")
        except LLMQuotaExhausted:
            await _fail(
                session, receipt, "The demo's daily extraction budget is used up. Try a sample receipt."
            )
        except LLMUnavailable:
            await _fail(
                session,
                receipt,
                "The extraction service is unavailable. Try again or use a sample receipt.",
            )
        except ExtractionFailed as exc:
            await _fail(session, receipt, str(exc))
        except Exception:
            logger.exception("receipt processing failed")
            await _fail(session, receipt, "The receipt could not be processed.")
