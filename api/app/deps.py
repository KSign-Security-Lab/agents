"""Request dependencies: identity and common lookups."""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.db.models import User, UserRole
from api.app.db.session import get_db
from api.app.services.security import decode_internal_token

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def current_user(
    db: DbSession,
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    """Resolve the caller from the internal token minted by the web tier."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = decode_internal_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token expired") from None
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"invalid token: {exc}") from None

    user = await db.get(User, UUID(claims["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "user not found or inactive")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


async def admin_user(user: CurrentUser) -> User:
    if user.role != UserRole.admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin role required")
    return user


AdminUser = Annotated[User, Depends(admin_user)]
