"""The ingestion pipeline: Source config -> RawOffers -> normalize -> dedupe ->
validate -> (optional) probe -> upsert Coupon -> IngestionRun audit row."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import metrics
from app.core.slugs import generate_unique_slug
from app.ingestion import dedupe, normalize, probe, validate
from app.ingestion.normalize import NormalizedOffer, NormalizeError
from app.ingestion.registry import get_adapter
from app.models.category import Category
from app.models.coupon import Coupon, CouponStatus
from app.models.ingestion_run import IngestionRun, RunStatus
from app.models.source import Source
from app.models.store import Store


async def _get_store(db: AsyncSession, offer: NormalizedOffer) -> Store:
    result = await db.execute(select(Store).where(Store.slug == offer.store_slug))
    store = result.scalar_one_or_none()
    if store is None:
        store = Store(
            name=offer.store_name,
            slug=offer.store_slug,
            website_url=offer.store_website,
        )
        db.add(store)
        await db.flush()  # assign PK before the coupon references it
    return store


async def _get_category(
    db: AsyncSession, offer: NormalizedOffer, source: Source
) -> Category | None:
    slug = offer.category_slug or source.config.get("default_category_slug")
    if not slug:
        return None
    result = await db.execute(select(Category).where(Category.slug == slug))
    category = result.scalar_one_or_none()
    if category is None and offer.category_name:
        result = await db.execute(select(Category).where(Category.name.ilike(offer.category_name)))
        category = result.scalar_one_or_none()
    return category


async def _find_existing(
    db: AsyncSession, source: Source, offer: NormalizedOffer, content_hash: str
) -> Coupon | None:
    if offer.external_id:
        result = await db.execute(
            select(Coupon).where(
                Coupon.source_id == source.id, Coupon.external_id == offer.external_id
            )
        )
        found = result.scalar_one_or_none()
        if found is not None:
            return found
    result = await db.execute(select(Coupon).where(Coupon.content_hash == content_hash))
    return result.scalar_one_or_none()


def _apply_status(
    coupon: Coupon, *, probe_ok: bool | None, probe_reason: str | None, now: datetime
) -> None:
    coupon.last_checked_at = now
    if coupon.expires_at is not None and coupon.expires_at <= now:
        coupon.status = CouponStatus.expired
        coupon.is_active = False
        coupon.failure_reason = "expired"
    elif probe_ok is False:
        coupon.status = CouponStatus.failed
        coupon.is_active = False
        coupon.failure_reason = (probe_reason or "destination unreachable")[:255]
    else:
        coupon.status = CouponStatus.active
        coupon.is_active = True
        coupon.failure_reason = None


async def run_ingestion(db: AsyncSession, source: Source) -> IngestionRun:
    run = IngestionRun(source_id=source.id, status=RunStatus.running, started_at=datetime.now(UTC))
    db.add(run)
    await db.flush()
    try:
        if not source.is_enabled:
            raise ValueError(f"Source {source.slug} is disabled")
        adapter = get_adapter(source.kind)
        raw_offers = await adapter.fetch(source.config)
        run.fetched = len(raw_offers)

        for raw in raw_offers:
            try:
                offer = normalize.normalize_offer(raw)
            except NormalizeError:
                run.failed += 1
                continue
            errors = validate.validate_offer(offer)
            if errors:
                run.failed += 1
                continue

            category = await _get_category(db, offer, source)
            if category is None:
                run.failed += 1
                continue
            store = await _get_store(db, offer)

            content_hash = dedupe.content_hash(offer)
            coupon = await _find_existing(db, source, offer, content_hash)

            probe_ok: bool | None = None
            probe_reason: str | None = None
            if source.probe_destinations:
                probe_ok, probe_reason = await probe.probe_destination(offer.destination_url)

            now = datetime.now(UTC)
            already_expired = offer.expires_at is not None and offer.expires_at <= now
            if coupon is None:
                coupon = Coupon(
                    title=offer.title,
                    slug=await generate_unique_slug(db, Coupon, offer.title),
                    code=offer.code,
                    description=offer.description,
                    discount_type=offer.discount_type,
                    discount_value=offer.discount_value,
                    store_id=store.id,
                    category_id=category.id,
                    destination_url=offer.destination_url,
                    expires_at=offer.expires_at,
                    is_active=not already_expired,
                    source_id=source.id,
                    external_id=offer.external_id,
                    content_hash=content_hash,
                )
                db.add(coupon)
                run.created += 1
            else:
                coupon.title = offer.title
                coupon.code = offer.code
                coupon.description = offer.description
                coupon.discount_type = offer.discount_type
                coupon.discount_value = offer.discount_value
                coupon.store_id = store.id
                coupon.category_id = category.id
                coupon.destination_url = offer.destination_url
                coupon.expires_at = offer.expires_at
                coupon.source_id = source.id
                coupon.external_id = offer.external_id
                coupon.content_hash = content_hash
                run.updated += 1

            # expires_at must be set before _apply_status reads it.
            await db.flush()
            _apply_status(coupon, probe_ok=probe_ok, probe_reason=probe_reason, now=now)

        run.status = RunStatus.success
        run.error = None
        # Labelled by source slug, not name: the slug is the stable identifier,
        # and a source renamed in the admin panel should not fork this series and
        # orphan the history of every alert that fired on it.
        metrics.ingestion_runs.labels(source.slug, RunStatus.success.value).inc()
    except Exception as exc:  # noqa: BLE001 - the run row must capture any failure
        await db.rollback()
        # Rollback also undid this run row's INSERT; record the failure in a fresh one.
        failed_run = IngestionRun(
            source_id=source.id,
            status=RunStatus.failed,
            started_at=run.started_at,
            finished_at=datetime.now(UTC),
            fetched=run.fetched,
            error=str(exc)[:2000],
        )
        db.add(failed_run)
        await db.commit()
        await db.refresh(failed_run)
        # Same slug label as the success path, so a failure rate is a ratio over
        # one label set rather than two unrelated series.
        metrics.ingestion_runs.labels(source.slug, RunStatus.failed.value).inc()
        return failed_run

    run.finished_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(run)
    return run
