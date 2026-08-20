"""Password hashing and the internal JWT the Next.js BFF presents to the API.

The browser never talks to this service directly: it holds an Auth.js session
cookie, and the BFF exchanges that for a short-lived internal token. That keeps
session management in one place (the web tier) while the API stays stateless.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt

from api.app.config import settings

INTERNAL_TOKEN_TTL = timedelta(minutes=settings.internal_jwt_ttl_minutes)

# bcrypt truncates at 72 bytes; passing a longer secret silently ignores the
# tail, so it is rejected rather than accepted with a false sense of strength.
MAX_PASSWORD_BYTES = 72


def hash_password(raw: str) -> str:
    """bcrypt directly rather than via passlib: passlib 1.7.4 probes
    ``bcrypt.__about__``, which bcrypt 4.x removed, and logs a traceback on every
    call even though hashing succeeds."""
    data = raw.encode("utf-8")
    if len(data) > MAX_PASSWORD_BYTES:
        raise ValueError(f"비밀번호는 최대 {MAX_PASSWORD_BYTES}바이트까지 지원합니다")
    return bcrypt.hashpw(data, bcrypt.gensalt()).decode("ascii")


def verify_password(raw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(raw.encode("utf-8")[:MAX_PASSWORD_BYTES],
                              hashed.encode("ascii"))
    except (ValueError, TypeError):
        # A malformed stored hash must read as "wrong password", not crash login.
        return False


def mint_internal_token(user_id: str, email: str, role: str,
                        ttl: timedelta = INTERNAL_TOKEN_TTL) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "email": email,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
        "iss": "agents-web",
    }
    return jwt.encode(payload, settings.internal_jwt_secret,
                      algorithm=settings.internal_jwt_algorithm)


def decode_internal_token(token: str) -> dict[str, Any]:
    return jwt.decode(
        token,
        settings.internal_jwt_secret,
        algorithms=[settings.internal_jwt_algorithm],
        issuer="agents-web",
        options={"require": ["exp", "sub"]},
    )
