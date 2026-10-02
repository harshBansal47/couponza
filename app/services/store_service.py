import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import paginate
from app.core.slugs import generate_unique_slug
from app.models.store import Store
from app.schemas.store import StoreCreate, StoreUpdate


async def list_stores(
    db: AsyncSession, *, search: str | None = None, skip: int = 0, limit: int = 20
) -> tuple[Sequence[Store], int]:
    stmt = select(Store)
    if search:
        stmt = stmt.where(Store.name.ilike(f"%{search}%"))
    return await paginate(db, stmt.order_by(Store.name), skip, limit)


async def get_store(db: AsyncSession, store_id: uuid.UUID) -> Store | None:
    return await db.get(Store, store_id)


async def get_store_by_slug(db: AsyncSession, slug: str) -> Store | None:
    result = await db.execute(select(Store).where(Store.slug == slug))
    return result.scalar_one_or_none()


async def create_store(db: AsyncSession, payload: StoreCreate) -> Store:
    store = Store(
        name=payload.name,
        slug=await generate_unique_slug(db, Store, payload.name),
        logo_url=payload.logo_url,
        website_url=payload.website_url,
        description=payload.description,
        commission_disclosure=payload.commission_disclosure,
    )
    db.add(store)
    await db.commit()
    await db.refresh(store)
    return store


async def update_store(db: AsyncSession, store: Store, payload: StoreUpdate) -> Store:
    data = payload.model_dump(exclude_unset=True)
    if "name" in data and data["name"] != store.name:
        data["slug"] = await generate_unique_slug(db, Store, data["name"], exclude_id=store.id)
    for field, value in data.items():
        setattr(store, field, value)
    await db.commit()
    await db.refresh(store)
    return store


async def delete_store(db: AsyncSession, store: Store) -> None:
    await db.delete(store)
    await db.commit()
