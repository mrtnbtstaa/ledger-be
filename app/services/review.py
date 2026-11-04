from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.db.models import Receipt
from app.schemas.extraction import Extracted
from app.services import confidence, validation
from app.services.pipeline import add_audit


def ensure_reviewable(receipt: Receipt) -> None:
    if receipt.status != "needs_review":
        raise ApiError(409, "invalid_state", "Only receipts awaiting review can be changed.")


def edit(session: AsyncSession, receipt: Receipt, data: Extracted) -> None:
    ensure_reviewable(receipt)
    checks = validation.run_checks(data, None)
    score = confidence.compute_confidence(checks, data, False)
    receipt.data = data.model_dump(mode="json", by_alias=True)
    receipt.checks = [check.model_dump() for check in checks]
    receipt.confidence = score
    receipt.edited = True
    add_audit(session, receipt.id, "edited", actor="reviewer", details={"confidence": score})


def approve(session: AsyncSession, receipt: Receipt) -> None:
    ensure_reviewable(receipt)
    failed = [
        check["rule"]
        for check in (receipt.checks or [])
        if not check.get("passed") and not check.get("skipped")
    ]
    receipt.status = "approved"
    add_audit(session, receipt.id, "approved", actor="reviewer", details={"failedChecks": failed})


def reject(session: AsyncSession, receipt: Receipt) -> None:
    ensure_reviewable(receipt)
    receipt.status = "rejected"
    add_audit(session, receipt.id, "rejected", actor="reviewer", details={})
