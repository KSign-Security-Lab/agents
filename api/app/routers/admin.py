"""User administration."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select

from api.app.db.models import User, UserRole
from api.app.deps import AdminUser, DbSession
from api.app.schemas import UserOut
from api.app.services.security import hash_password

router = APIRouter(prefix="/admin", tags=["admin"])


class UserCreate(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8)
    role: UserRole = UserRole.member


class UserUpdate(BaseModel):
    name: str | None = None
    password: str | None = Field(default=None, min_length=8)
    role: UserRole | None = None
    is_active: bool | None = None


def _out(u: User) -> UserOut:
    return UserOut(id=u.id, email=u.email, name=u.name, role=u.role.value)


@router.get("/users", response_model=list[UserOut])
async def list_users(db: DbSession, admin: AdminUser) -> list[UserOut]:
    users = (await db.execute(select(User).order_by(User.created_at))).scalars().all()
    return [_out(u) for u in users]


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(body: UserCreate, db: DbSession, admin: AdminUser) -> UserOut:
    email = body.email.lower()
    if (await db.execute(select(User).where(User.email == email))).scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, "이미 등록된 이메일입니다")
    user = User(email=email, name=body.name.strip(),
                password_hash=hash_password(body.password), role=body.role)
    db.add(user)
    await db.flush()
    return _out(user)


@router.patch("/users/{user_id}", response_model=UserOut)
async def update_user(user_id: UUID, body: UserUpdate, db: DbSession,
                      admin: AdminUser) -> UserOut:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "사용자를 찾을 수 없습니다")

    if body.name is not None:
        user.name = body.name.strip()
    if body.password:
        user.password_hash = hash_password(body.password)
    if body.role is not None:
        # Removing the last admin would lock everyone out of user management.
        if user.role == UserRole.admin and body.role != UserRole.admin:
            remaining = await db.scalar(
                select(User.id).where(User.role == UserRole.admin, User.id != user_id).limit(1)
            )
            if remaining is None:
                raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                    "마지막 관리자의 권한은 변경할 수 없습니다")
        user.role = body.role
    if body.is_active is not None:
        user.is_active = body.is_active
    await db.flush()
    return _out(user)
