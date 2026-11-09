from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_HASHER = PasswordHasher(
    time_cost=2,
    memory_cost=32768,
    parallelism=1,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)
# A fixed valid hash keeps unknown-email logins as slow as known-email logins.
_DUMMY_HASH = (
    "$argon2id$v=19$m=32768,t=2,p=1$KAIILrk/FG4eYw7ESUrtNw$"
    "RKZUcu3kattmgMFklLu1vCFoGzrV3PMR7/xH4CnndBA"
)


def hash_password(password: str) -> str:
    return _HASHER.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _HASHER.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def verify_dummy(password: str) -> None:
    verify_password(password, _DUMMY_HASH)


def needs_rehash(password_hash: str) -> bool:
    try:
        return _HASHER.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True
