from typing import Any
from typing import cast as type_cast
from uuid import UUID

from sqlalchemy import Numeric, cast, delete, func, or_, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.db.models import Receipt, ReceiptFile

_LEDGER_STATUSES = ("auto_approved", "approved")


def not_found() -> ApiError:
    return ApiError(404, "receipt_not_found", "That receipt does not exist.")


async def get_owned(session: AsyncSession, owner_id: UUID, receipt_id: UUID) -> Receipt:
    receipt = await session.scalar(
        select(Receipt).where(
            Receipt.id == receipt_id,
            Receipt.user_id == owner_id,
            Receipt.is_sample.is_(False),
        )
    )
    if receipt is None:
        raise not_found()
    return receipt


async def get_visible(
    session: AsyncSession, receipt_id: UUID, owner_id: UUID | None
) -> Receipt:
    receipt = await session.get(Receipt, receipt_id)
    if receipt is None:
        raise not_found()
    if receipt.is_sample:
        return receipt
    if owner_id is None:
        raise ApiError(401, "unauthenticated", "Sign in to continue.")
    if receipt.user_id != owner_id:
        raise not_found()
    return receipt


async def get_sample_by_sample_id(session: AsyncSession, sample_id: str) -> Receipt | None:
    return await session.scalar(
        select(Receipt)
        .where(Receipt.sample_id == sample_id, Receipt.is_sample.is_(True))
        .order_by(Receipt.uploaded_at.desc())
        .limit(1)
    )


async def find_by_hash(session: AsyncSession, owner_id: UUID, sha256: str) -> Receipt | None:
    return await session.scalar(
        select(Receipt).where(
            Receipt.user_id == owner_id,
            Receipt.file_sha256 == sha256,
            Receipt.is_sample.is_(False),
        )
    )


async def list_owned(
    session: AsyncSession,
    owner_id: UUID,
    *,
    status: str | None = None,
    category: str | None = None,
    q: str | None = None,
    sort: str = "date",
    order: str = "desc",
    limit: int = 50,
    offset: int = 0,
    ledger: bool = False,
) -> tuple[list[Receipt], int]:
    conditions: list[Any] = [
        Receipt.user_id == owner_id,
        Receipt.is_sample.is_(False),
    ]
    if ledger:
        conditions.append(Receipt.status.in_(_LEDGER_STATUSES))
    if status is not None:
        conditions.append(Receipt.status == status)
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
    return list(rows), total


async def export_rows(session: AsyncSession, owner_id: UUID, *, ledger: bool) -> list[Receipt]:
    conditions: list[Any] = [
        Receipt.user_id == owner_id,
        Receipt.is_sample.is_(False),
    ]
    if ledger:
        conditions.append(Receipt.status.in_(_LEDGER_STATUSES))
    rows = (
        (
            await session.execute(
                select(Receipt).where(*conditions).order_by(Receipt.uploaded_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def get_file(session: AsyncSession, receipt_id: UUID) -> ReceiptFile | None:
    return await session.get(ReceiptFile, receipt_id)


async def delete_file(session: AsyncSession, receipt_id: UUID) -> None:
    await session.execute(delete(ReceiptFile).where(ReceiptFile.receipt_id == receipt_id))


async def delete_owned(session: AsyncSession, owner_id: UUID, receipt_id: UUID) -> None:
    result = type_cast(
        CursorResult[Any],
        await session.execute(
            delete(Receipt).where(
                Receipt.id == receipt_id,
                Receipt.user_id == owner_id,
                Receipt.is_sample.is_(False),
            )
        ),
    )
    if not result.rowcount:
        raise not_found()


async def delete_all_owned(session: AsyncSession, owner_id: UUID) -> int:
    result = type_cast(
        CursorResult[Any],
        await session.execute(
            delete(Receipt).where(
                Receipt.user_id == owner_id,
                Receipt.is_sample.is_(False),
            )
        ),
    )
    return result.rowcount or 0
