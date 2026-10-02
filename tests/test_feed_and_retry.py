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
