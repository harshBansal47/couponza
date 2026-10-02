"""Tests for the JSON feed adapter, retry helper, and probe retry behaviour."""

import httpx
import pytest

from app.ingestion import probe
from app.ingestion.retry import with_retries
from app.ingestion.sources.feed import FeedSource

pytestmark = pytest.mark.asyncio


def _mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_feed_source_parses_list_payload():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "title": "Feed Deal",
                    "store_slug": "ajio",
                    "category_slug": "fashion",
                    "discount_text": "30% off",
                    "destination_url": "https://www.ajio.com/x",
                }
            ],
        )

    source = FeedSource(client=_mock_client(handler))
    offers = await source.fetch({"url": "https://feed.example.com/offers.json"})
    assert len(offers) == 1
    assert offers[0].title == "Feed Deal"


async def test_feed_source_parses_wrapped_payload():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"offers": [{"title": "A", "destination_url": "https://x.co"}]}
        )

    source = FeedSource(client=_mock_client(handler))
    offers = await source.fetch({"url": "https://feed.example.com/offers.json"})
    assert len(offers) == 1


async def test_feed_source_requires_url():
    with pytest.raises(ValueError):
        await FeedSource().fetch({})


async def test_feed_source_retries_transient_failure(monkeypatch):
    calls = {"n": 0}

    async def _no_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr("asyncio.sleep", _no_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(500)
        return httpx.Response(200, json=[])

    source = FeedSource(client=_mock_client(handler))
    offers = await source.fetch({"url": "https://feed.example.com/x.json"})
    assert offers == []
    assert calls["n"] == 3


async def test_with_retries_exhausts_and_raises():
    attempts = {"n": 0}

    async def _always_fail():
        attempts["n"] += 1
        raise ConnectionError("boom")

    with pytest.raises(ConnectionError):
        await with_retries(_always_fail, attempts=3, base_delay=0, retryable=(ConnectionError,))
    assert attempts["n"] == 3


async def test_probe_retries_5xx_then_succeeds(monkeypatch):
    async def _no_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500 if calls["n"] < 2 else 200)

    # Patch the AsyncClient constructor used inside probe_destination.
    real_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    ok, reason = await probe.probe_destination("https://example.com/deal")
    assert ok is True
    assert reason is None
    assert calls["n"] == 2


async def test_probe_gives_up_after_retries(monkeypatch):
    async def _no_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503)

    real_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    ok, reason = await probe.probe_destination("https://example.com/dead")
    assert ok is False
    assert reason is not None
    assert calls["n"] == 3

# ---- Awin adapter ----
#
# The Awin adapter is the first real affiliate network integration. It must
# handle pagination, auth, field mapping, and error resilience. These tests
# verify the adapter produces well-formed RawOffers that the normalizer accepts.

import httpx
import pytest

from app.ingestion.sources.awin import AwinSource


@pytest.mark.asyncio
async def test_awin_source_maps_voucher_to_raw_offer():
    # A minimal voucher payload that exercises the happy path.
    voucher = {
        "id": "12345",
        "title": "20% off everything",
        "description": "Site-wide discount",
        "voucherCode": "SAVE20",
        "deepLink": "https://store.awin.com/click?ref=123",
        "endDate": "2025-12-31",
        "discountType": "percentage",
        "discountPercentage": 20.0,
        "advertiserName": "Test Store",
        "advertiserId": 999,
        "advertiserUrl": "https://teststore.com",
    }
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/publishers/123/vouchers"
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json={"results": [voucher]})
        return httpx.Response(200, json={"results": []})

    source = AwinSource(client=_mock_client(handler))
    offers = await source.fetch(
        {
            "api_key": "test_key",
            "publisher_id": 123,
            "deal_types": ["coupon"],
        }
    )

    assert len(offers) == 1
    offer = offers[0]
    assert offer.title == "20% off everything"
    assert offer.code == "SAVE20"
    assert offer.destination_url == "https://store.awin.com/click?ref=123"
    assert offer.discount_type == "percentage"
    assert offer.discount_value == 20.0
    assert offer.store_slug == "test-store"
    assert offer.store_name == "Test Store"
    assert offer.external_id == "12345"


@pytest.mark.asyncio
async def test_awin_source_skips_voucher_without_code():
    # Vouchers without codes are not actionable for our coupon model.
    voucher = {
        "id": "12346",
        "title": "Free shipping",
        "voucherCode": None,
        "deepLink": "https://store.awin.com/click?ref=456",
    }
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json={"results": [voucher]})
        return httpx.Response(200, json={"results": []})

    source = AwinSource(client=_mock_client(handler))
    offers = await source.fetch(
        {
            "api_key": "test_key",
            "publisher_id": 123,
            "deal_types": ["coupon"],
        }
    )

    assert offers == []


@pytest.mark.asyncio
async def test_awin_source_maps_deal_to_raw_offer():
    # Deals have no code, just a deep link.
    deal = {
        "id": "12347",
        "title": "Up to 50% off sale",
        "description": "Seasonal sale",
        "dealType": "sale",
        "deepLink": "https://store.awin.com/sale?ref=789",
        "endDate": "2025-11-30",
        "dealType": "percentage",
        "discountPercentage": 50.0,
        "advertiserName": "Sale Store",
    }
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        assert "/deals" in str(request.url)
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json={"results": [deal]})
        return httpx.Response(200, json={"results": []})

    source = AwinSource(client=_mock_client(handler))
    offers = await source.fetch(
        {
            "api_key": "test_key",
            "publisher_id": 123,
            "deal_types": ["sale"],
        }
    )

    assert len(offers) == 1
    offer = offers[0]
    assert offer.title == "Up to 50% off sale"
    assert offer.code is None
    assert offer.destination_url == "https://store.awin.com/sale?ref=789"
    assert offer.discount_type == "percentage"
    assert offer.discount_value == 50.0


@pytest.mark.asyncio
async def test_awin_source_paginates_through_multiple_pages():
    # The adapter should keep fetching until an empty page.
    page1 = [{"id": str(i), "title": f"Deal {i}", "voucherCode": f"CODE{i}", "deepLink": f"https://x/{i}"} for i in range(3)]
    page2 = [{"id": "3", "title": "Deal 3", "voucherCode": "CODE3", "deepLink": "https://x/3"}]
    page3: list[dict] = []

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json={"results": page1})
        if calls["n"] == 2:
            return httpx.Response(200, json={"results": page2})
        return httpx.Response(200, json={"results": page3})

    source = AwinSource(client=_mock_client(handler))
    offers = await source.fetch(
        {
            "api_key": "test_key",
            "publisher_id": 123,
            "deal_types": ["coupon"],
            "page_size": 2,  # force pagination
        }
    )

    assert len(offers) == 4
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_awin_source_filters_by_currency_and_region():
    # The adapter should pass currency and regions as query params.
    captured: dict[str, Any] = {}
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        captured["params"] = dict(request.url.params)
        if calls["n"] == 1:
            return httpx.Response(200, json={"results": []})
        return httpx.Response(200, json={"results": []})

    source = AwinSource(client=_mock_client(handler))
    await source.fetch(
        {
            "api_key": "test_key",
            "publisher_id": 123,
            "currency": "GBP",
            "regions": ["GB", "IE"],
        }
    )

    assert captured["params"]["currency"] == "GBP"
    assert captured["params"]["regions"] == "GB,IE"


@pytest.mark.asyncio
async def test_awin_source_requires_credentials():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"results": []})

    source = AwinSource(client=_mock_client(handler))
    with pytest.raises(ValueError, match="requires 'api_key' and 'publisher_id'"):
        await source.fetch({})

    with pytest.raises(ValueError, match="requires 'api_key' and 'publisher_id'"):
        await source.fetch({"api_key": "x"})


# Helper reused from existing tests
def _mock_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))
