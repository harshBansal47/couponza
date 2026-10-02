"""End-to-end tests of the ingestion pipeline against the in-memory SQLite DB."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.ingestion import pipeline, probe
from app.ingestion.lifecycle import expire_stale_coupons
from app.ingestion.normalize import NormalizeError, normalize_offer, offer_from_dict, parse_discount
from app.models.category import Category
from app.models.coupon import Coupon, CouponStatus
from app.models.ingestion_run import IngestionRun, RunStatus
from app.models.source import Source, SourceKind
from app.models.store import Store

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def category(async_session_maker):
    async with async_session_maker() as db:
        cat = Category(name="Fashion", slug="fashion")
        db.add(cat)
        await db.commit()
        await db.refresh(cat)
        return cat


def _offer(**overrides):
    base = {
        "title": "Flat 50% Off on Kurtas",
        "store_slug": "myntra",
        "store_name": "Myntra",
        "category_slug": "fashion",
        "code": "kurt a50",
        "discount_text": "50% off",
        "destination_url": "https://www.myntra.com/offers/kurta?deal=1",
        "expires_at": (datetime.now(UTC) + timedelta(days=7)).strftime("%Y-%m-%d"),
        "external_id": "offer-123",
    }
    base.update(overrides)
    # codes in tests are upper-cased by normalize; normalize also strips spaces,
    # but what we really want is a code WITHOUT spaces, so fix the default.
    if base.get("code") == "kurt a50":
        base["code"] = "KURTA50"
    return offer_from_dict(base)


@pytest.fixture
async def static_source(async_session_maker, category):
    offers = [
        {
            "title": "Flat 50% Off on Kurtas",
            "store_slug": "myntra",
            "store_name": "Myntra",
            "category_slug": "fashion",
            "code": "KURTA50",
            "discount_text": "50% off",
            "destination_url": "https://www.myntra.com/offers/kurta",
            "expires_at": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
            "external_id": "offer-123",
        },
        {
            "title": "Free shipping on orders above 499",
            "store_slug": "myntra",
            "category_slug": "fashion",
            "discount_text": "free shipping",
            "destination_url": "https://www.myntra.com/shipping",
            "expires_at": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
            "external_id": "offer-124",
        },
    ]
    async with async_session_maker() as db:
        source = Source(
            name="Seed",
            slug="seed",
            kind=SourceKind.static,
            config={"offers": offers},
            is_enabled=True,
        )
        db.add(source)
        await db.commit()
        await db.refresh(source)
        return source


async def test_parse_discount():
    from app.models.coupon import DiscountType

    assert parse_discount("50% off") == (DiscountType.percentage, 50.0)
    assert parse_discount("₹200 off on first order") == (DiscountType.fixed, 200.0)
    assert parse_discount("BOGO on tees") == (DiscountType.deal, None)
    assert parse_discount("") is None


async def test_normalize_rejects_bad_offer():
    raw = _offer(destination_url="", title="")
    with pytest.raises(NormalizeError):
        normalize_offer(raw)


async def test_pipeline_creates_coupons_and_run(async_session_maker, static_source):
    async with async_session_maker() as db:
        run = await pipeline.run_ingestion(db, static_source)
        assert run.status == RunStatus.success
        assert run.fetched == 2
        assert run.created == 2

        coupons = (await db.execute(select(Coupon))).scalars().all()
        assert len(coupons) == 2
        assert all(c.status == CouponStatus.active for c in coupons)
        assert all(c.is_active for c in coupons)
        myntra = (await db.execute(select(Store).where(Store.slug == "myntra"))).scalars().one()
        assert all(c.store_id == myntra.id for c in coupons)


async def test_pipeline_dedupes_on_second_run(async_session_maker, static_source):
    async with async_session_maker() as db:
        await pipeline.run_ingestion(db, static_source)
        run2 = await pipeline.run_ingestion(db, static_source)
        assert run2.created == 0
        assert run2.updated == 2
        assert len((await db.execute(select(Coupon))).scalars().all()) == 2


async def test_pipeline_marks_expired_offers(async_session_maker, category):
    async with async_session_maker() as db:
        source = Source(
            name="Old",
            slug="old",
            kind=SourceKind.static,
            config={
                "offers": [
                    {
                        "title": "Expired deal",
                        "store_slug": "flipkart",
                        "category_slug": "fashion",
                        "discount_text": "10% off",
                        "destination_url": "https://www.flipkart.com/x",
                        "expires_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
                    }
                ]
            },
        )
        db.add(source)
        await db.commit()
        await db.refresh(source)
        run = await pipeline.run_ingestion(db, source)
        assert run.created == 1
        coupon = (await db.execute(select(Coupon))).scalars().one()
        assert coupon.status == CouponStatus.expired
        assert coupon.is_active is False
        assert coupon.failure_reason == "expired"


async def test_pipeline_failed_probe_marks_offer_failed(async_session_maker, category, monkeypatch):
    async def fake_probe(url: str):
        return False, "destination returned HTTP 404"

    monkeypatch.setattr(probe, "probe_destination", fake_probe)
    monkeypatch.setattr(pipeline.probe, "probe_destination", fake_probe)

    async with async_session_maker() as db:
        source = Source(
            name="Probed",
            slug="probed",
            kind=SourceKind.static,
            probe_destinations=True,
            config={
                "offers": [
                    {
                        "title": "Some deal",
                        "store_slug": "ajio",
                        "category_slug": "fashion",
                        "discount_text": "20% off",
                        "destination_url": "https://www.ajio.com/dead",
                    }
                ]
            },
        )
        db.add(source)
        await db.commit()
        await db.refresh(source)
        await pipeline.run_ingestion(db, source)
        coupon = (await db.execute(select(Coupon))).scalars().one()
        assert coupon.status == CouponStatus.failed
        assert coupon.failure_reason == "destination returned HTTP 404"
        assert coupon.last_checked_at is not None


async def test_pipeline_skips_unknown_category(async_session_maker, static_source):
    async with async_session_maker() as db:
        static_source.config = {
            "offers": [
                {
                    "title": "Voucher",
                    "store_slug": "amazon",
                    "category_slug": "nonexistent",
                    "destination_url": "https://www.amazon.in/x",
                }
            ]
        }
        run = await pipeline.run_ingestion(db, static_source)
        assert run.failed == 1
        assert run.created == 0


async def test_expire_stale_coupons(async_session_maker, static_source):
    async with async_session_maker() as db:
        await pipeline.run_ingestion(db, static_source)
        coupon = (await db.execute(select(Coupon).limit(1))).scalars().one()
        coupon.expires_at = datetime.now(UTC) - timedelta(hours=1)
        await db.commit()
        expired = await expire_stale_coupons(db)
        assert expired == 1
        await db.refresh(coupon)
        assert coupon.status == CouponStatus.expired
        assert coupon.is_active is False


async def test_ingestion_run_recorded_on_disabled_source(async_session_maker, static_source):
    async with async_session_maker() as db:
        static_source.is_enabled = False
        await db.commit()
        run = await pipeline.run_ingestion(db, static_source)
        assert run.status == RunStatus.failed
        assert "disabled" in (run.error or "")
        runs = (await db.execute(select(IngestionRun))).scalars().all()
        assert len(runs) >= 1


async def test_content_hash_ignores_discount_changes():
    from app.ingestion import dedupe

    offer_a = normalize_offer(
        offer_from_dict(
            {
                "title": "Sale",
                "store_slug": "x",
                "code": "ABC",
                "discount_text": "10% off",
                "destination_url": "https://x.com",
            }
        )
    )
    offer_b = normalize_offer(
        offer_from_dict(
            {
                "title": "Sale",
                "store_slug": "x",
                "code": "ABC",
                "discount_text": "70% off",
                "destination_url": "https://x.com",
            }
        )
    )
    assert dedupe.content_hash(offer_a) == dedupe.content_hash(offer_b)
