import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from app.config import Settings
from app.core.errors import ApiError

_ALGORITHM = "HS256"


def _kid(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()[:16]


def create_access_token(user_id: str, settings: Settings) -> tuple[str, int]:
    now = datetime.now(UTC)
    expires = now + timedelta(minutes=settings.access_token_minutes)
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": expires,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(
        payload,
        settings.jwt_secret,
        algorithm=_ALGORITHM,
        headers={"kid": _kid(settings.jwt_secret)},
    )
    return token, int((expires - now).total_seconds())


def decode_access_token(token: str, settings: Settings) -> dict[str, Any]:
    if not settings.jwt_secret:
        raise ApiError(503, "auth_unavailable", "Sign-in is not configured on this server.")

    keys = [(settings.jwt_secret, _kid(settings.jwt_secret))]
    if settings.jwt_secret_previous:
        keys.append((settings.jwt_secret_previous, _kid(settings.jwt_secret_previous)))

    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as exc:
        raise ApiError(401, "unauthenticated", "Sign in to continue.") from exc

    kid = header.get("kid")
    candidates = [key for key, key_kid in keys if kid is None or kid == key_kid]
    if not candidates:
        candidates = [key for key, _ in keys]

    last_error: Exception | None = None
    for key in candidates:
        try:
            return jwt.decode(
                token,
                key,
                algorithms=[_ALGORITHM],
                audience=settings.jwt_audience,
                issuer=settings.jwt_issuer,
                options={"require": ["exp", "iat", "sub", "iss", "aud"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise ApiError(
                401, "token_expired", "Your session expired. Refresh and try again."
            ) from exc
        except jwt.InvalidTokenError as exc:
            last_error = exc

    raise ApiError(401, "unauthenticated", "Sign in to continue.") from last_error
