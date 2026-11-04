from fastapi import Header

from app.core.security import check_passcode
from app.db.session import get_session

__all__ = ["get_session", "require_passcode"]


def require_passcode(x_review_passcode: str | None = Header(None, alias="X-Review-Passcode")) -> None:
    check_passcode(x_review_passcode)
