"""Create the first admin account from ADMIN_EMAIL / ADMIN_PASSWORD.

Idempotent: running it again resets the password rather than failing, which is
also how you recover a forgotten one.
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from api.app.config import settings
from api.app.db.models import User, UserRole
from api.app.db.session import SessionLocal
from api.app.services.security import hash_password


async def main() -> None:
    email = settings.admin_email.lower()
    name = settings.admin_name
    password = settings.admin_password

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

    print(f"admin {action}: {email} / {password}")


if __name__ == "__main__":
    asyncio.run(main())
