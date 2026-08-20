"""Create the first admin account.

Idempotent: running it again updates the password rather than failing, which is
also how you recover a forgotten one.
"""
from __future__ import annotations

import asyncio
import os
import secrets

from sqlalchemy import select

from api.app.db.models import User, UserRole
from api.app.db.session import SessionLocal
from api.app.services.security import hash_password


async def main() -> None:
    email = (os.environ.get("ADMIN_EMAIL") or "keonoh@ksign.com").lower()
    name = os.environ.get("ADMIN_NAME") or "관리자"
    password = os.environ.get("ADMIN_PASSWORD") or secrets.token_urlsafe(12)
    generated = "ADMIN_PASSWORD" not in os.environ

    async with SessionLocal() as db:
        user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if user is None:
            user = User(email=email, name=name, password_hash=hash_password(password),
                        role=UserRole.admin)
            db.add(user)
            action = "created"
        else:
            user.password_hash = hash_password(password)
            user.role = UserRole.admin
            user.is_active = True
            action = "updated"
        await db.commit()

    print(f"admin {action}: {email}")
    if generated:
        print(f"password: {password}")
        print("(set ADMIN_PASSWORD to choose your own)")


if __name__ == "__main__":
    asyncio.run(main())
