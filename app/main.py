import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded

from app.api import admin, health, receipts, samples, upload
from app.config import get_settings
from app.core.errors import (
    ApiError,
    api_error_handler,
    rate_limit_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from app.core.logging import RequestIdMiddleware, configure_logging
from app.core.rate_limit import limiter
from app.db.session import SessionLocal
from app.services import retention
from app.services.pipeline import fail_stuck

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    try:
        async with SessionLocal() as session:
            await fail_stuck(session)
            await retention.purge(session, settings)
    except Exception:
        logger.warning("startup recovery skipped: database unavailable")
    yield


settings = get_settings()
configure_logging(settings.log_level)

app = FastAPI(
    title="Receipt Processor API",
    version=settings.app_version,
    lifespan=lifespan,
)
app.state.limiter = limiter
limiter.enabled = settings.rate_limit_enabled

app.add_middleware(RequestIdMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)

app.add_exception_handler(ApiError, api_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)
app.add_exception_handler(RateLimitExceeded, rate_limit_handler)
app.add_exception_handler(Exception, unhandled_error_handler)

app.include_router(health.router)
app.include_router(admin.router)
app.include_router(upload.router)
app.include_router(samples.router)
app.include_router(receipts.router)
