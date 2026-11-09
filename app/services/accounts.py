import asyncio
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core import passwords, tokens
from app.core.errors import ApiError
from app.db.models import RefreshToken, User, UserUsage
from app.repos import tokens as token_repo
from app.repos import users as user_repo
from app.schemas.auth import TokenPair, UserOut


def _invalid() -> ApiError:
    return ApiError(401, "invalid_credentials", "The email or password is incorrect.")


def _require_auth(settings: Settings) -> None:
    if not settings.jwt_secret:
        raise ApiError(503, "auth_unavailable", "Sign-in is not configured on this server.")


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _check_password(email: str, password: str) -> None:
    if len(password) < 10 or len(password) > 128:
        raise ApiError(
            422,
            "weak_password",
            "Password must be between 10 and 128 characters.",
        )
    if password.strip().lower() == email:
        raise ApiError(422, "weak_password", "Password must be different from the email.")


async def _new_refresh_token(
    session: AsyncSession,
    user_id: uuid.UUID,
    settings: Settings,
    family_id: uuid.UUID | None = None,
) -> tuple[str, RefreshToken]:
    plain = secrets.token_urlsafe(32)
    row = RefreshToken(
        user_id=user_id,
        token_hash=token_repo.hash_token(plain),
        family_id=family_id or uuid.uuid4(),
        expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_days),
    )
    session.add(row)
    await session.flush()
    return plain, row


def _pair(user: User, access: str, refresh: str, expires_in: int) -> TokenPair:
    return TokenPair(
        user=UserOut(id=str(user.id), email=user.email, createdAt=user.created_at),
        accessToken=access,
        refreshToken=refresh,
        expiresIn=expires_in,
    )


async def _issue(
    session: AsyncSession,
    user: User,
    settings: Settings,
    family_id: uuid.UUID | None = None,
) -> TokenPair:
    access, expires_in = tokens.create_access_token(str(user.id), settings)
    refresh, _ = await _new_refresh_token(session, user.id, settings, family_id)
    return _pair(user, access, refresh, expires_in)


async def register(
    session: AsyncSession, email: str, password: str, settings: Settings
) -> TokenPair:
    _require_auth(settings)
    if not settings.registration_enabled or await user_repo.count(session) >= settings.max_users:
        raise ApiError(403, "registration_closed", "Registration is closed right now.")
    normalized = _normalize_email(email)
    _check_password(normalized, password)
    if await user_repo.get_by_email(session, normalized) is not None:
        raise ApiError(409, "email_taken", "That email is already registered.")

    user = User(email=normalized, password_hash=passwords.hash_password(password))
    user_repo.add(session, user)
    try:
        await session.flush()
        result = await _issue(session, user, settings)
        await session.commit()
        return result
    except IntegrityError as exc:
        await session.rollback()
        raise ApiError(409, "email_taken", "That email is already registered.") from exc


async def login(
    session: AsyncSession, email: str, password: str, settings: Settings
) -> TokenPair:
    _require_auth(settings)
    normalized = _normalize_email(email)
    user = await user_repo.get_by_email(session, normalized)
    if user is None:
        passwords.verify_dummy(password)
        raise _invalid()

    now = datetime.now(UTC)
    if user.locked_until is not None and user.locked_until > now:
        # Slow repeated failures without locking the real owner out for long.
        await asyncio.sleep(min(2 ** min(user.failed_login_count, 3), 5))

    if not passwords.verify_password(password, user.password_hash):
        user.failed_login_count += 1
        user.locked_until = now + timedelta(
            seconds=min(2 ** min(user.failed_login_count, 6), 60)
        )
        await session.commit()
        raise _invalid()

    if passwords.needs_rehash(user.password_hash):
        user.password_hash = passwords.hash_password(password)
    user.failed_login_count = 0
    user.locked_until = None
    result = await _issue(session, user, settings)
    await session.commit()
    return result


async def refresh(
    session: AsyncSession, refresh_token: str, settings: Settings
) -> TokenPair:
    _require_auth(settings)
    row = await token_repo.get_by_hash(session, token_repo.hash_token(refresh_token))
    if row is None:
        raise ApiError(401, "refresh_invalid", "Your session is no longer valid. Sign in again.")

    now = datetime.now(UTC)
    if row.expires_at <= now:
        raise ApiError(401, "refresh_invalid", "Your session is no longer valid. Sign in again.")

    user = await user_repo.get_by_id(session, row.user_id)
    if user is None:
        await token_repo.revoke_family(session, row.family_id, now)
        await session.commit()
        raise ApiError(401, "refresh_invalid", "Your session is no longer valid. Sign in again.")

    if row.revoked_at is not None:
        grace = timedelta(seconds=settings.refresh_reuse_grace_seconds)
        if row.replaced_by is not None and row.revoked_at > now - grace:
            # Two tabs refreshed at once. Issue a sibling without revoking the family.
            row.last_used_at = now
            result = await _issue(session, user, settings, row.family_id)
            await session.commit()
            return result
        await token_repo.revoke_family(session, row.family_id, now)
        await session.commit()
        raise ApiError(401, "refresh_invalid", "Your session is no longer valid. Sign in again.")

    result = await _issue(session, user, settings, row.family_id)
    new_row = await token_repo.get_by_hash(
        session, token_repo.hash_token(result.refresh_token)
    )
    row.revoked_at = now
    row.last_used_at = now
    row.replaced_by = new_row.id if new_row is not None else None
    await session.commit()
    return result


async def logout(session: AsyncSession, refresh_token: str) -> None:
    row = await token_repo.get_by_hash(session, token_repo.hash_token(refresh_token))
    if row is not None:
        await token_repo.revoke_family(session, row.family_id)
        await session.commit()


async def delete_account(session: AsyncSession, user: User, password: str) -> None:
    if not passwords.verify_password(password, user.password_hash):
        raise ApiError(401, "invalid_credentials", "The password is incorrect.")
    await token_repo.revoke_all_for_user(session, user.id)
    await user_repo.delete(session, user)
    await session.commit()


async def usage(session: AsyncSession, user: User, settings: Settings) -> UserUsage | None:
    return await session.get(UserUsage, (user.id, datetime.now(UTC).date()))
