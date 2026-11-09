from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5433/receipts"

    @field_validator("database_url")
    @classmethod
    def _normalize_database_url(cls, value: str) -> str:
        """Accept Neon's postgres URL and asyncpg's ssl parameter without manual edits."""
        value = value.strip()
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                value = "postgresql+asyncpg://" + value[len(prefix) :]
                break
        parts = urlsplit(value)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        if "ssl" not in query and "sslmode" in query:
            query["ssl"] = query["sslmode"]
        query.pop("sslmode", None)
        query.pop("channel_binding", None)
        return urlunsplit(parts._replace(query=urlencode(query)))

    groq_api_key: str = ""
    groq_vision_model: str = "qwen/qwen3.8-27b"
    groq_text_model: str = "openai/gpt-oss-120b"
    groq_fallback_model: str = "openai/gpt-oss-20b"
    llm_timeout_seconds: float = 45
    llm_max_retries: int = 2
    llm_daily_call_budget: int = 200

    auto_approve_threshold: float = 0.85
    max_file_bytes: int = 5 * 1024 * 1024
    max_pdf_pages: int = 3
    file_retention_hours: int = 24
    record_retention_days: int = 7
    processing_timeout_seconds: int = 120

    rate_limit_enabled: bool = True
    rate_limit_upload: str = "5/minute;30/day"
    rate_limit_read: str = "60/minute"
    rate_limit_review: str = "20/minute"
    rate_limit_auth: str = "10/minute"
    rate_limit_register: str = "3/hour"
    user_daily_upload_limit: int = 20

    jwt_secret: str = ""
    jwt_secret_previous: str = ""
    jwt_issuer: str = "ledgerline-api"
    jwt_audience: str = "ledgerline-web"
    access_token_minutes: int = 15
    refresh_token_days: int = 14
    refresh_reuse_grace_seconds: int = 10
    registration_enabled: bool = True
    max_users: int = 200
    uploads_enabled: bool = True
    inactive_account_days: int = 180

    allowed_origins: str = (
        "http://localhost:3000,http://127.0.0.1:3000,https://ledger-fe-xi.vercel.app"
    )
    admin_token: str = ""
    public_base_url: str = ""
    google_service_account_json: str = ""
    google_sheet_id: str = ""
    google_sheet_tab: str = "Receipts"
    app_version: str = "0.1.0"
    env: str = "development"
    log_level: str = "INFO"

    @property
    def origins(self) -> list[str]:
        if not self.allowed_origins:
            return []
        return [origin.strip().rstrip("/") for origin in self.allowed_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
