import os

os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres:postgres@localhost:5433/receipts_test"
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["GROQ_API_KEY"] = ""

import httpx
import pytest

from app.db.models import Base
from app.db.session import engine
from app.main import app


@pytest.fixture(autouse=True)
async def clean_db():
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    # Each pytest-asyncio test gets a fresh event loop; pooled connections cannot cross loops.
    await engine.dispose()


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
