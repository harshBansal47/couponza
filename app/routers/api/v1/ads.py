import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import require_role
from app.core.pagination import Paginated
from app.models.ad import Ad, AdPosition
from app.models.user import Role, User
from app.schemas.ad import AdCreate, AdRead, AdUpdate
from app.services import ad_service

router = APIRouter(prefix="/ads", tags=["ads"])


@router.get("", response_model=Paginated[AdRead])
async def list_ads(
    position: AdPosition | None = None,
    active_only: bool = True,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> Paginated[AdRead]:
    items, total = await ad_service.list_ads(
        db, position=position, active_only=active_only, skip=skip, limit=limit
    )
    return Paginated(
        items=[AdRead.model_validate(item) for item in items],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.get("/{ad_id}", response_model=AdRead)
async def get_ad(ad_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> Ad:
    ad = await ad_service.get_ad(db, ad_id)
    if ad is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ad not found")
    return ad


@router.post("", response_model=AdRead, status_code=status.HTTP_201_CREATED)
async def create_ad(
    payload: AdCreate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_role(Role.admin, Role.editor)),
) -> Ad:
    return await ad_service.create_ad(db, payload)


@router.patch("/{ad_id}", response_model=AdRead)
async def update_ad(
    ad_id: uuid.UUID,
    payload: AdUpdate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_role(Role.admin, Role.editor)),
) -> Ad:
    ad = await ad_service.get_ad(db, ad_id)
    if ad is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ad not found")
    return await ad_service.update_ad(db, ad, payload)


@router.delete("/{ad_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ad(
    ad_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_role(Role.admin)),
) -> None:
    ad = await ad_service.get_ad(db, ad_id)
    if ad is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ad not found")
    await ad_service.delete_ad(db, ad)
