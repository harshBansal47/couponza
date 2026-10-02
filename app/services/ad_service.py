import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import paginate
from app.models.ad import Ad, AdPosition
from app.schemas.ad import AdCreate, AdUpdate


async def list_ads(
    db: AsyncSession,
    *,
    position: AdPosition | None = None,
    active_only: bool = True,
    skip: int = 0,
    limit: int = 20,
) -> tuple[Sequence[Ad], int]:
    stmt = select(Ad)
    if position is not None:
        stmt = stmt.where(Ad.position == position)
    if active_only:
        stmt = stmt.where(Ad.is_active.is_(True))
    return await paginate(db, stmt.order_by(Ad.created_at.desc()), skip, limit)


async def get_ad(db: AsyncSession, ad_id: uuid.UUID) -> Ad | None:
    return await db.get(Ad, ad_id)


async def create_ad(db: AsyncSession, payload: AdCreate) -> Ad:
    ad = Ad(**payload.model_dump())
    db.add(ad)
    await db.commit()
    await db.refresh(ad)
    return ad


async def update_ad(db: AsyncSession, ad: Ad, payload: AdUpdate) -> Ad:
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(ad, field, value)
    await db.commit()
    await db.refresh(ad)
    return ad


async def delete_ad(db: AsyncSession, ad: Ad) -> None:
    await db.delete(ad)
    await db.commit()
