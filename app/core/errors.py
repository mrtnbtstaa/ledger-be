import logging
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded

logger = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        retry_after: int | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retry_after = retry_after


def error_body(request: Request, code: str, message: str, retry_after: int | None = None) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "retryAfter": retry_after,
            "requestId": getattr(request.state, "request_id", None),
        }
    }


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=error_body(request, exc.code, exc.message, exc.retry_after),
    )


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Never log validation inputs: auth request bodies can contain passwords.
    logger.info(
        "request validation failed: %s",
        [(error.get("loc"), error.get("type")) for error in exc.errors()],
    )
    return JSONResponse(
        status_code=422,
        content=error_body(request, "validation_error", "Some fields were invalid."),
    )


async def rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content=error_body(request, "rate_limited", "Too many requests. Try again shortly.", 60),
        headers={"Retry-After": "60"},
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled error")
    return JSONResponse(
        status_code=500,
        content=error_body(request, "internal_error", "The server could not finish the request."),
    )
