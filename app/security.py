"""Password hashing and JWT handling.

bcrypt is used directly rather than through passlib: passlib's bcrypt backend
probes `bcrypt.__about__.__version__`, which no longer exists on bcrypt >= 4,
and it fails loudly at import time. Calling the library directly removes that
whole class of breakage.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
from jose import JWTError, jwt

from app.config import settings

# bcrypt silently truncates input beyond 72 bytes; reject instead of truncating
# so two different long passwords can never authenticate against each other.
MAX_PASSWORD_BYTES = 72
_MIN_PASSWORD_LENGTH = 8

_secret = settings.resolve_secret()


class AuthError(Exception):
    """Raised when a credential or token is invalid."""


def hash_password(password: str) -> str:
    validate_password_strength(password)
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    if not password or not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        # A malformed stored hash must read as "wrong password", never as a crash.
        return False


def validate_password_strength(password: str) -> None:
    if len(password) < _MIN_PASSWORD_LENGTH:
        raise AuthError(f"Password must be at least {_MIN_PASSWORD_LENGTH} characters.")
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise AuthError("Password is too long (max 72 bytes).")


def create_access_token(subject: str, extra: dict[str, Any] | None = None) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.access_token_expire_minutes)).timestamp()),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, _secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, _secret, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        raise AuthError("Invalid or expired token.") from exc
