import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import EntityNotFoundError
from app.core.pagination import paginate
from app.core.slugs import generate_unique_slug
from app.models.category import Category
from app.models.coupon import Coupon
from app.models.coupon_verification import CouponVerification
from app.models.store import Store
from app.schemas.coupon import CouponCreate, CouponUpdate

VERIFICATION_COOLDOWN = timedelta(hours=24)


async def list_coupons(
    db: AsyncSession,
    *,
    store_id: uuid.UUID | None = None,
    category_id: uuid.UUID | None = None,
    search: str | None = None,
    active_only: bool = True,
    skip: int = 0,
    limit: int = 20,
) -> tuple[Sequence[Coupon], int]:
    stmt = select(Coupon)
    if store_id is not None:
        stmt = stmt.where(Coupon.store_id == store_id)
    if category_id is not None:
        stmt = stmt.where(Coupon.category_id == category_id)
    if search:
        stmt = stmt.where(Coupon.title.ilike(f"%{search}%"))
    if active_only:
        stmt = stmt.where(Coupon.is_active.is_(True))
    return await paginate(db, stmt.order_by(Coupon.created_at.desc()), skip, limit)


async def get_coupon(db: AsyncSession, coupon_id: uuid.UUID) -> Coupon | None:
    return await db.get(Coupon, coupon_id)


async def get_coupon_by_slug(db: AsyncSession, slug: str) -> Coupon | None:
    result = await db.execute(select(Coupon).where(Coupon.slug == slug))
    return result.scalar_one_or_none()


async def _ensure_store_and_category(
    db: AsyncSession, store_id: uuid.UUID | None, category_id: uuid.UUID | None
) -> None:
    """Give a clean 404 instead of a raw foreign-key error from the database."""
    if store_id is not None and await db.get(Store, store_id) is None:
        raise EntityNotFoundError(f"Store {store_id} not found")
    if category_id is not None and await db.get(Category, category_id) is None:
        raise EntityNotFoundError(f"Category {category_id} not found")


async def create_coupon(db: AsyncSession, payload: CouponCreate, created_by: uuid.UUID) -> Coupon:
    await _ensure_store_and_category(db, payload.store_id, payload.category_id)

    coupon = Coupon(
        title=payload.title,
        slug=await generate_unique_slug(db, Coupon, payload.title),
        code=payload.code,
        description=payload.description,
        discount_type=payload.discount_type,
        discount_value=payload.discount_value,
        store_id=payload.store_id,
        category_id=payload.category_id,
        destination_url=payload.destination_url,
        expires_at=payload.expires_at,
        created_by=created_by,
    )
    db.add(coupon)
    await db.commit()
    await db.refresh(coupon)
    return coupon


async def update_coupon(db: AsyncSession, coupon: Coupon, payload: CouponUpdate) -> Coupon:
    data = payload.model_dump(exclude_unset=True)
    await _ensure_store_and_category(db, data.get("store_id"), data.get("category_id"))

    if "title" in data and data["title"] != coupon.title:
        data["slug"] = await generate_unique_slug(db, Coupon, data["title"], exclude_id=coupon.id)

    for field, value in data.items():
        setattr(coupon, field, value)
    await db.commit()
    await db.refresh(coupon)
    return coupon


async def delete_coupon(db: AsyncSession, coupon: Coupon) -> None:
    await db.delete(coupon)
    await db.commit()


async def record_click(db: AsyncSession, coupon: Coupon) -> None:
    coupon.clicks_count += 1
    await db.commit()


class AlreadyVerifiedRecentlyError(Exception):
    """Raised when the same visitor already voted on this coupon within the cooldown."""


async def record_verification(
    db: AsyncSession,
    coupon: Coupon,
    *,
    worked: bool,
    ip_hash: str,
    note: str | None = None,
) -> Coupon:
    cutoff = datetime.now(UTC) - VERIFICATION_COOLDOWN
    recent = await db.execute(
        select(CouponVerification).where(
            CouponVerification.coupon_id == coupon.id,
            CouponVerification.ip_hash == ip_hash,
            CouponVerification.created_at >= cutoff,
        )
    )
    if recent.scalar_one_or_none() is not None:
        raise AlreadyVerifiedRecentlyError

    db.add(
        CouponVerification(coupon_id=coupon.id, ip_hash=ip_hash, worked=worked, note=note or None)
    )
    if worked:
        coupon.success_count += 1
    else:
        coupon.fail_count += 1
    coupon.last_verified_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(coupon)
    return coupon


async def get_verification_history(
    db: AsyncSession, coupon_id: uuid.UUID, limit: int = 50
) -> Sequence[CouponVerification]:
    result = await db.execute(
        select(CouponVerification)
        .where(CouponVerification.coupon_id == coupon_id)
        .order_by(CouponVerification.created_at.desc())
        .limit(limit)
    )
    return result.scalars().all()
