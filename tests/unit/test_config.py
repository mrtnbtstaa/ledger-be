from app.config import Settings


def test_database_url_normalizes_neon() -> None:
    settings = Settings(
        database_url="postgresql://u:p@host/db?sslmode=require&channel_binding=require"
    )
    assert settings.database_url == "postgresql+asyncpg://u:p@host/db?ssl=require"


def test_database_url_keeps_asyncpg_ssl() -> None:
    settings = Settings(database_url="postgresql+asyncpg://u:p@host/db?ssl=require")
    assert settings.database_url == "postgresql+asyncpg://u:p@host/db?ssl=require"


def test_database_url_accepts_postgres_prefix() -> None:
    settings = Settings(database_url="postgres://u:p@host/db")
    assert settings.database_url == "postgresql+asyncpg://u:p@host/db"
