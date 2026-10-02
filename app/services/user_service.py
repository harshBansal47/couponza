from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.models.user import Role, User
from app.schemas.user import UserCreate, UserUpdate


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def create_user(db: AsyncSession, user_in: UserCreate, role: Role = Role.user) -> User:
    user = User(
        email=user_in.email,
        hashed_password=hash_password(user_in.password),
        full_name=user_in.full_name,
        role=role,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def update_user(db: AsyncSession, user: User, user_in: "UserUpdate") -> User:
    """Apply a profile edit. A password change requires the current one."""
    if user_in.password is not None:
        if user_in.current_password is None or not verify_password(
            user_in.current_password, user.hashed_password
        ):
            raise ValueError("Current password is incorrect")
        user.hashed_password = hash_password(user_in.password)

    if user_in.full_name is not None:
        user.full_name = user_in.full_name or None

    await db.commit()
    await db.refresh(user)
    return user
