import hmac

from app.config import get_settings
from app.core.errors import ApiError


def check_passcode(provided: str | None) -> None:
    expected = get_settings().review_passcode
    if not expected:
        return
    if not provided or not hmac.compare_digest(provided, expected):
        raise ApiError(401, "unauthorized", "A review passcode is required.")


def check_admin_token(authorization: str | None) -> None:
    expected = get_settings().admin_token
    if not expected:
        raise ApiError(401, "unauthorized", "Admin access is not configured.")
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(token, expected):
        raise ApiError(401, "unauthorized", "A valid admin token is required.")
