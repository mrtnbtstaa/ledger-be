import base64
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import Receipt, SheetSync
from app.services.pipeline import add_audit

logger = logging.getLogger(__name__)

_SYNC_STATES = ("pending", "failed")
_MAX_BACKOFF_SECONDS = 3600


async def enqueue(session: AsyncSession, receipt_id: UUID) -> None:
    if await session.get(SheetSync, receipt_id) is None:
        session.add(SheetSync(receipt_id=receipt_id, state="pending"))


def _credentials(settings: Settings) -> Any | None:
    raw = settings.google_service_account_json.strip()
    if not raw or not settings.google_sheet_id:
        return None
    # Accept a JSON string, base64, or a path to the service-account file.
    path = Path(raw)
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return json.loads(base64.b64decode(raw))


def _worksheet(settings: Settings) -> Any | None:
    info = _credentials(settings)
    if info is None:
        return None
    import gspread

    client = gspread.service_account_from_dict(info)
    return client.open_by_key(settings.google_sheet_id).worksheet(settings.google_sheet_tab)


def _retry_due(row: SheetSync, now: datetime) -> bool:
    if row.state != "failed":
        return True
    delay = min(_MAX_BACKOFF_SECONDS, 60 * (2 ** max(0, row.attempts - 1)))
    return row.updated_at <= now - timedelta(seconds=delay)


def _value(data: dict[str, Any], key: str) -> Any:
    value = data.get(key)
    return "" if value is None else value


def _row(receipt: Receipt) -> list[Any]:
    data = receipt.data or {}
    return [
        str(receipt.id),
        _value(data, "date"),
        _value(data, "vendor"),
        _value(data, "category"),
        _value(data, "subtotal"),
        _value(data, "tax"),
        _value(data, "total"),
        _value(data, "currency"),
        receipt.status,
    ]


async def sync_pending(session: AsyncSession, settings: Settings, limit: int = 20) -> int:
    rows = (
        (
            await session.execute(
                select(SheetSync)
                .where(SheetSync.state.in_(_SYNC_STATES))
                .order_by(SheetSync.updated_at)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return 0

    try:
        worksheet = _worksheet(settings)
    except Exception:
        logger.exception("google sheets setup failed")
        return 0
    if worksheet is None:
        return 0

    now = datetime.now(UTC)
    synced = 0
    for row in rows:
        if not _retry_due(row, now):
            continue
        receipt = await session.get(Receipt, row.receipt_id)
        if receipt is None or receipt.status not in ("approved", "auto_approved"):
            row.state = "failed"
            row.attempts += 1
            row.last_error = "Receipt is missing or not approved."
            row.updated_at = now
            await session.commit()
            continue

        try:
            # shortcut: scans column A for idempotency; fine at demo row counts.
            ids = worksheet.col_values(1)
            if str(receipt.id) not in ids:
                # RAW keeps untrusted receipt text from being evaluated as a formula.
                worksheet.append_row(_row(receipt), value_input_option="RAW")
            row.state = "synced"
            row.attempts += 1
            row.last_error = None
            row.updated_at = now
            add_audit(session, receipt.id, "sheet_synced", details={"row": _row(receipt)})
            await session.commit()
            synced += 1
        except Exception as exc:
            row.state = "failed"
            row.attempts += 1
            row.last_error = str(exc)[:1000]
            row.updated_at = now
            add_audit(session, receipt.id, "sheet_sync_failed", details={"error": row.last_error})
            await session.commit()
            logger.warning("sheet sync failed for %s: %s", receipt.id, exc)
    return synced
