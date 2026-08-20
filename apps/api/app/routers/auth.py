"""Login and identity.

The browser holds an Auth.js session cookie against the web tier; the web tier
exchanges credentials here for a short-lived internal token it then presents on
every API call. Keeping session state in one place (the web tier) lets the API
stay stateless.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from api.app.db.models import User
from api.app.deps import CurrentUser, DbSession
from api.app.schemas import LoginRequest, LoginResponse, UserOut
from api.app.services.security import (
    INTERNAL_TOKEN_TTL,
    mint_internal_token,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _user_out(u: User) -> UserOut:
    return UserOut(id=u.id, email=u.email, name=u.name, role=u.role.value)


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, db: DbSession) -> LoginResponse:
    user = (await db.execute(
        select(User).where(User.email == body.email.lower())
    )).scalar_one_or_none()

    # Same error either way: distinguishing them would let anyone enumerate
    # which addresses have accounts.
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "이메일 또는 비밀번호가 올바르지 않습니다")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "비활성화된 계정입니다")

    return LoginResponse(
        user=_user_out(user),
        token=mint_internal_token(str(user.id), user.email, user.role.value),
        expires_in=int(INTERNAL_TOKEN_TTL.total_seconds()),
    )


@router.post("/refresh", response_model=LoginResponse)
async def refresh(user: CurrentUser) -> LoginResponse:
    """Re-mint the internal token so a long chat session does not expire mid-answer."""
    return LoginResponse(
        user=_user_out(user),
        token=mint_internal_token(str(user.id), user.email, user.role.value),
        expires_in=int(INTERNAL_TOKEN_TTL.total_seconds()),
    )


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> UserOut:
    return _user_out(user)
