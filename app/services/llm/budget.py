from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import LLMBudget
from app.services.llm.client import LLMQuotaExhausted


async def reserve(session: AsyncSession, settings: Settings) -> None:
    day = datetime.now(UTC).date()
    statement = (
        pg_insert(LLMBudget)
        .values(day=day, calls=1)
        .on_conflict_do_update(
            index_elements=[LLMBudget.day],
            set_={"calls": LLMBudget.calls + 1},
            where=LLMBudget.calls < settings.llm_daily_call_budget,
        )
        .returning(LLMBudget.calls)
    )
    row = (await session.execute(statement)).scalar_one_or_none()
    await session.commit()
    if row is None:
        raise LLMQuotaExhausted()
