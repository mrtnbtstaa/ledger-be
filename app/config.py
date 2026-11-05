from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5433/receipts"

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

    allowed_origins: str = "https://ledger-fe-xi.vercel.app,"
    review_passcode: str = ""
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
