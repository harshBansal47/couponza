import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import require_role
from app.core.exceptions import EntityNotFoundError
from app.core.limiter import limiter
from app.core.pagination import Paginated
from app.core.request_meta import client_ip, hash_ip
from app.models.coupon import Coupon
from app.models.user import Role, User
from app.schemas.coupon import CouponCreate, CouponRead, CouponUpdate
from app.schemas.coupon_public import (
    CouponPublicRead,
    VerificationHistoryItem,
    VerifyRequest,
    VerifyResponse,
    compute_success_rate,
)
from app.services import affiliate, click_service, coupon_service
from app.services.coupon_service import AlreadyVerifiedRecentlyError

router = APIRouter(prefix="/coupons", tags=["coupons"])


# ---------------------------------------------------------------- public reads
# Every read below returns CouponPublicRead: no destination_url. The real URL
# is only ever resolved by GET /go/{coupon_id}, which redirects instead of
# returning JSON — so a visitor (human or AI agent) can browse freely, but
# can't scrape the raw affiliate link straight out of the API response.


@router.get("", response_model=Paginated[CouponPublicRead])
async def list_coupons(
    store_id: uuid.UUID | None = None,
    category_id: uuid.UUID | None = None,
    search: str | None = None,
    active_only: bool = True,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> Paginated[CouponPublicRead]:
    items, total = await coupon_service.list_coupons(
        db,
        store_id=store_id,
        category_id=category_id,
        search=search,
        active_only=active_only,
        skip=skip,
        limit=limit,
    )
    return Paginated(
        items=[CouponPublicRead.model_validate(item) for item in items],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.get("/by-slug/{slug}", response_model=CouponPublicRead)
async def get_coupon_by_slug(slug: str, db: AsyncSession = Depends(get_db)) -> Coupon:
    coupon = await coupon_service.get_coupon_by_slug(db, slug)
    if coupon is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Coupon not found")
    return coupon


@router.get("/{coupon_id}", response_model=CouponPublicRead)
async def get_coupon(coupon_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> Coupon:
    coupon = await coupon_service.get_coupon(db, coupon_id)
    if coupon is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Coupon not found")
    return coupon


@router.get("/{coupon_id}/go", response_class=RedirectResponse, status_code=status.HTTP_302_FOUND)
async def go_to_coupon(
    coupon_id: uuid.UUID,
    request: Request,
    src: str | None = Query(default=None, max_length=40),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """The only place destination_url is ever revealed: a redirect, not a JSON field.

    Each hit is recorded as a ClickEvent and the outbound URL carries that
    click's reference, so a later network sale can be traced back to this
    coupon and to the page (`?src=`) that sent the visitor.
    """
    coupon = await coupon_service.get_coupon(db, coupon_id)
    if coupon is None or not coupon.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Coupon not found")
    store = await click_service.get_store(db, coupon.store_id)
    click, _ = await click_service.record_click(
        db,
        store=store,
        coupon=coupon,
        src=src,
        visitor_hash=hash_ip(client_ip(request)),
        user_agent=request.headers.get("user-agent"),
    )
    target = affiliate.build_outbound_url(
        coupon.destination_url,
        network=store.affiliate_network if store else None,
        link_template=store.link_template if store else None,
        clickref=click.clickref,
    )
    return RedirectResponse(target, status_code=status.HTTP_302_FOUND)


@router.post("/{coupon_id}/verify", response_model=VerifyResponse)
@limiter.limit("30/minute")
async def verify_coupon(
    coupon_id: uuid.UUID,
    payload: VerifyRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> VerifyResponse:
    """Anonymous, rate-limited "worked" / "didn't work" reports — the community trust signal."""
    coupon = await coupon_service.get_coupon(db, coupon_id)
    if coupon is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Coupon not found")
    try:
        updated = await coupon_service.record_verification(
            db,
            coupon,
            worked=payload.worked,
            ip_hash=hash_ip(client_ip(request)),
            note=payload.note,
        )
    except AlreadyVerifiedRecentlyError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="You've already reported on this coupon recently. Try again later.",
        ) from exc

    return VerifyResponse(
        success_count=updated.success_count,
        fail_count=updated.fail_count,
        last_verified_at=updated.last_verified_at,
        success_rate=compute_success_rate(updated.success_count, updated.fail_count),
    )


@router.get("/{coupon_id}/verification-history", response_model=list[VerificationHistoryItem])
async def get_verification_history(
    coupon_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> list[VerificationHistoryItem]:
    """Get verification history for a coupon (for trust timeline display)."""
    coupon = await coupon_service.get_coupon(db, coupon_id)
    if coupon is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Coupon not found")
    history = await coupon_service.get_verification_history(db, coupon_id, limit)
    return [VerificationHistoryItem.model_validate(v) for v in history]


# ------------------------------------------------------------- staff management
# These stay on the full CouponRead (destination_url included) — the person
# creating/editing the coupon obviously needs to see the URL they just typed.


@router.post("", response_model=CouponRead, status_code=status.HTTP_201_CREATED)
async def create_coupon(
    payload: CouponCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(Role.admin, Role.editor)),
) -> Coupon:
    try:
        return await coupon_service.create_coupon(db, payload, current_user.id)
    except EntityNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.patch("/{coupon_id}", response_model=CouponRead)
async def update_coupon(
    coupon_id: uuid.UUID,
    payload: CouponUpdate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_role(Role.admin, Role.editor)),
) -> Coupon:
    coupon = await coupon_service.get_coupon(db, coupon_id)
    if coupon is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Coupon not found")
    try:
        return await coupon_service.update_coupon(db, coupon, payload)
    except EntityNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.delete("/{coupon_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_coupon(
    coupon_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_role(Role.admin)),
) -> None:
    coupon = await coupon_service.get_coupon(db, coupon_id)
    if coupon is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Coupon not found")
    await coupon_service.delete_coupon(db, coupon)
