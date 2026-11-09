import os

os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5433/receipts_test"
)
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["GROQ_API_KEY"] = ""
os.environ["JWT_SECRET"] = "test-secret-0123456789abcdef0123456789abcdef"
os.environ["UPLOADS_ENABLED"] = "true"

import httpx
import pytest

from app.db.models import Base
from app.db.session import engine
from app.main import app

PASSWORD = "correct horse battery"


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


async def register(client: httpx.AsyncClient, email: str) -> dict:
    response = await client.post(
        "/auth/register", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 201, response.text
    return response.json()


def bearer(tokens: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['accessToken']}"}


@pytest.fixture
async def owner_tokens(client) -> dict:
    return await register(client, "owner@example.com")


@pytest.fixture
async def other_tokens(client) -> dict:
    return await register(client, "other@example.com")


@pytest.fixture
def owner_headers(owner_tokens) -> dict[str, str]:
    return bearer(owner_tokens)


@pytest.fixture
def other_headers(other_tokens) -> dict[str, str]:
    return bearer(other_tokens)
