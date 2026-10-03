from typing import Any

import httpx

from app.ingestion.base import RawOffer
from app.ingestion.normalize import offer_from_dict
from app.ingestion.retry import with_retries

_TIMEOUT = httpx.Timeout(15.0, connect=5.0)
_USER_AGENT = "CouponbaseBot/1.0 (+https://couponbase.example.com/bot)"


class FeedSource:
    """Fetches offers from a JSON URL. This is the future of real ingestion:
    `config = {"url": "https://…/offers.json"}` yields either a JSON list of
    offer objects or `{"offers": [...]}`. Optional `headers` for API-key auth.
    """

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    async def fetch(self, config: dict[str, Any]) -> list[RawOffer]:
        url = config.get("url")
        if not url:
            raise ValueError("feed source config requires a 'url'")
        headers = {"User-Agent": _USER_AGENT, **(config.get("headers") or {})}
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=_TIMEOUT, headers=headers)

        async def _get() -> httpx.Response:
            response = await client.get(url, headers=headers if owns_client else None)
            response.raise_for_status()
            return response

        try:
            response = await with_retries(_get, retryable=(httpx.HTTPError,))
        finally:
            if owns_client:
                await client.aclose()

        payload = response.json()
        items = payload.get("offers", payload) if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            raise TypeError("feed JSON must be a list or contain an 'offers' list")
        return [offer_from_dict(item) for item in items]
