from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditEvent


def add(
    session: AsyncSession,
    receipt_id: UUID,
    event_type: str,
    actor: str = "system",
    details: dict[str, Any] | None = None,
) -> None:
    session.add(AuditEvent(receipt_id=receipt_id, type=event_type, actor=actor, details=details or {}))
