"""Coupon lifecycle work the scheduler owns.

Thin service layer over `app.ingestion` so the scheduler doesn't need to know
that ingestion has a pipeline, adapters and a run ledger — and so the same
functions are callable from an admin "run now" button without reaching into
the ingestion package.

The two jobs:

* **Expire** is pure bookkeeping. Cheap, safe to run hourly, and it is the
  difference between a shopper seeing "No longer valid" and a shopper being
  sent to a dead code.
* **Refresh** re-runs every enabled source. This is the expensive one, and it
  is intentionally not called more often than `ingestion_interval_hours`.
"""

import logging
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import metrics
from app.ingestion.lifecycle import expire_stale_coupons as _expire_stale_coupons
from app.ingestion.pipeline import run_ingestion
from app.models.coupon import Coupon, CouponStatus
from app.models.source import Source

logger = logging.getLogger("couponza.lifecycle")


async def expire_stale_coupons(db: AsyncSession) -> int:
    expired = await _expire_stale_coupons(db)
    await _publish_stale_gauge(db)
    if expired:
        # Adding zero to a counter is harmless but shows up in the series as a
        # no-op event, and a sweep that retires nothing is not an event.
        metrics.coupons_expired.inc(expired)
    return expired


async def _publish_stale_gauge(db: AsyncSession) -> None:
    """Publish how many coupons are past their expiry but still marked active.

    A non-zero value here that keeps growing means the expiry job has stopped
    working while the site keeps serving dead codes — which is the exact failure
    this whole subsystem exists to make visible.

    Set here rather than in the /metrics handler because the expiry job has
    already issued this query; re-running it per scrape would put a table scan
    on the metrics path.
    """
    still_active = await db.execute(
        select(func.count())
        .select_from(Coupon)
        .where(
            Coupon.status == CouponStatus.active,
            Coupon.expires_at.is_not(None),
            Coupon.expires_at <= datetime.now(UTC),
        )
    )
    metrics.stale_coupons.set(int(still_active.scalar_one()))


async def refresh_coupon_prices(db: AsyncSession) -> int:
    """Re-ingest every enabled source. Returns the number of successful runs.

    A source that fails is logged and skipped rather than aborting the sweep:
    one dead affiliate feed must not stop every other store from updating.
    """
    result = await db.execute(select(Source).where(Source.is_enabled.is_(True)))
    sources = list(result.scalars().all())

    succeeded = 0
    for source in sources:
        try:
            run_row = await run_ingestion(db, source)
        except Exception:
            logger.exception("source ingestion failed", extra={"source": source.slug})
            continue
        if run_row.status.value == "success":
            succeeded += 1
        logger.info(
            "source ingested",
            extra={
                "source": source.slug,
                "status": run_row.status.value,
                # Not `created`/`updated`: those are reserved LogRecord
                # attributes, and logging raises rather than silently dropping
                # the field, which turns a naming slip into an outage.
                "offers_created": run_row.created,
                "offers_updated": run_row.updated,
                "offers_failed": run_row.failed,
            },
        )
    return succeeded
