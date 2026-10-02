"""Evaluate tracked products against their latest observation and fire alerts.

Called by scripts/run_alerts.py (cron). Sends at most one alert per
(tracked product, price point) pair — either the target-met condition, a
price drop, or a new coupon, in that priority order."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.coupon import Coupon
from app.models.product import PricePoint, Product
from app.models.tracking import (
    AlertEvent,
    AlertKind,
    NotificationPreference,
    TrackAlertState,
    TrackedProduct,
)
from app.models.user import User
from app.services import notifications
from app.services.price_service import effective_price


async def _get_or_create_state(db: AsyncSession, tracked_id: uuid.UUID) -> TrackAlertState:
    result = await db.execute(
        select(TrackAlertState).where(TrackAlertState.tracked_product_id == tracked_id)
    )
    state = result.scalar_one_or_none()
    if state is None:
        state = TrackAlertState(tracked_product_id=tracked_id)
        db.add(state)
        await db.flush()
    return state


async def evaluate_tracked_product(db: AsyncSession, tracked: TrackedProduct) -> list[AlertEvent]:
    points = (
        (
            await db.execute(
                select(PricePoint)
                .where(PricePoint.product_id == tracked.product_id)
                .order_by(PricePoint.captured_at.desc())
                .limit(2)
            )
        )
        .scalars()
        .all()
    )
    if not points:
        return []
    latest = points[0]
    previous = points[1] if len(points) > 1 else None

    state = await _get_or_create_state(db, tracked.id)
    if state.last_price_point_id == latest.id and state.alerts_sent > 0:
        return []  # already told this user about this observation

    product = await db.get(Product, tracked.product_id)
    coupon = await db.get(Coupon, latest.coupon_id) if latest.coupon_id else None
    if product is None:
        return []

    # Priority: cheapest-legitimate-price signal beats a mere price drop beats
    # a new coupon. One alert per observation keeps users from being spammed.
    eff = effective_price(latest, coupon)
    kind: AlertKind | None = None
    detail: str | None = None

    if tracked.target_price is not None and eff <= float(tracked.target_price):
        kind = AlertKind.target_met
        detail = f"{product.name}: effective price {eff} hit your target of {tracked.target_price}"
    elif previous is not None and float(latest.price) < float(previous.price):
        kind = AlertKind.price_drop
        detail = f"{product.name}: price dropped from {previous.price} to {latest.price}"
    elif latest.coupon_id is not None and (previous is None or previous.coupon_id is None):
        kind = AlertKind.coupon_appeared
        detail = f"{product.name}: a coupon now applies — effective price {eff}"

    if kind is None:
        state.last_price_point_id = latest.id
        await db.commit()
        return []

    user = await db.get(User, tracked.user_id)
    if user is None:
        return []
    prefs_result = await db.execute(
        select(NotificationPreference).where(NotificationPreference.user_id == user.id)
    )
    prefs = prefs_result.scalar_one_or_none()
    if prefs is None:
        prefs = NotificationPreference(user_id=user.id)
        db.add(prefs)
        await db.flush()

    events: list[AlertEvent] = []
    for sender in notifications.senders_for(prefs):
        await sender.send(user, prefs, detail or "")
        event = AlertEvent(
            user_id=user.id,
            tracked_product_id=tracked.id,
            kind=kind,
            channel=sender.channel,
            detail=detail,
            price_point_id=latest.id,
        )
        db.add(event)
        events.append(event)

    state.last_price_point_id = latest.id
    state.last_alert_kind = kind.value
    state.alerts_sent += len(events)
    await db.commit()
    return events


async def run_alert_scan(db: AsyncSession) -> int:
    result = await db.execute(select(TrackedProduct))
    total = 0
    for tracked in result.scalars().all():
        total += len(await evaluate_tracked_product(db, tracked))
    return total
