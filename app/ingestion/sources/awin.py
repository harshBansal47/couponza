"""Awin (Affiliate Window) API adapter.

Fetches voucher/deal data from the Awin API and converts to RawOffers.

Awin API docs: https://api.awin.com/
- Base URL: https://api.awin.com
- Auth: API key in header or query param
- Voucher feed: /publishers/{publisherId}/advertisers/{advertiserId}/vouchers
- Deals feed: /publishers/{publisherId}/advertisers/{advertiserId}/deals
- Pagination: page/limit params

Config schema:
{
    "api_key": "your_awin_api_key",
    "publisher_id": 12345,
    "advertiser_ids": [123, 456],  # optional, defaults to all approved
    "program_status": "active",   # "active" | "pending" | "all"
    "deal_types": ["coupon", "deal", "sale"],  # what to fetch
    "currency": "USD",            # filter by currency
    "regions": ["US", "GB", "DE", "FR"], # filter by region
    "page_size": 100              # items per page
}
"""

from typing import Any

import httpx

from app.ingestion.base import RawOffer
from app.ingestion.retry import with_retries

_AWIN_BASE = "https://api.awin.com"
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_USER_AGENT = "CouponzaBot/1.0 (+https://couponza.example.com/bot)"


class AwinSource:
    """Fetches vouchers/deals from the Awin Publisher API.

    Awin's voucher/deal endpoints return offers with fields like:
    - title, description, voucherCode, startDate, endDate
    - advertiserName, advertiserId, deepLink (destination URL)
    - discountAmount, discountType, discountPercentage
    - currency, regions, categories
    """

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    async def fetch(self, config: dict[str, Any]) -> list[RawOffer]:
        api_key = config.get("api_key")
        publisher_id = config.get("publisher_id")
        if not api_key or not publisher_id:
            raise ValueError("awin source config requires 'api_key' and 'publisher_id'")

        advertiser_ids = config.get("advertiser_ids") or []
        program_status = config.get("program_status", "active")
        deal_types = config.get("deal_types", ["coupon", "deal", "sale"])
        currency = config.get("currency")
        regions = config.get("regions") or []
        _page_size = config.get("page_size", 100)

        headers = {
            "User-Agent": _USER_AGENT,
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }

        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=_TIMEOUT, headers=headers)

        async def _fetch_page(
            fetch_endpoint: str, fetch_params: dict[str, Any]
        ) -> list[dict[str, Any]]:
            url = f"{_AWIN_BASE}{fetch_endpoint}"
            response = await client.get(url, params=fetch_params)
            response.raise_for_status()
            data = response.json()
            return data.get("results", [])

        all_raw: list[RawOffer] = []

        try:
            # Fetch vouchers (coupon codes)
            if "coupon" in deal_types or "deal" in deal_types:
                voucher_params = {
                    "programStatus": program_status,
                    "voucherType": "voucher",
                }
                if currency:
                    voucher_params["currency"] = currency
                if regions:
                    voucher_params["regions"] = ",".join(regions)

                for adv_id in advertiser_ids or [""]:
                    fetch_endpoint = (
                        f"/publishers/{publisher_id}/advertisers/{adv_id}/vouchers"
                        if adv_id
                        else f"/publishers/{publisher_id}/vouchers"
                    )
                    page = 0
                    while True:
                        fetch_params = {**voucher_params, "page": page, "pageSize": _page_size}
                        results = await with_retries(
                            lambda ep=fetch_endpoint, fp=fetch_params: _fetch_page(ep, fp),
                            retryable=(httpx.HTTPError,),
                        )
                        if not results:
                            break
                        for item in results:
                            raw = self._map_voucher(item)
                            if raw:
                                all_raw.append(raw)
                        page += 1

            # Fetch deals/sales (no code, just deep link)
            if "sale" in deal_types or "deal" in deal_types:
                deal_params = {
                    "programStatus": program_status,
                    "dealType": "deal",
                }
                if currency:
                    deal_params["currency"] = currency
                if regions:
                    deal_params["regions"] = ",".join(regions)

                for adv_id in advertiser_ids or [""]:
                    fetch_endpoint = (
                        f"/publishers/{publisher_id}/advertisers/{adv_id}/deals"
                        if adv_id
                        else f"/publishers/{publisher_id}/deals"
                    )
                    page = 0
                    while True:
                        fetch_params = {**deal_params, "page": page, "pageSize": _page_size}
                        results = await with_retries(
                            lambda ep=fetch_endpoint, fp=fetch_params: _fetch_page(ep, fp),
                            retryable=(httpx.HTTPError,),
                        )
                        if not results:
                            break
                        for item in results:
                            raw = self._map_deal(item)
                            if raw:
                                all_raw.append(raw)
                        page += 1

        finally:
            if owns_client:
                await client.aclose()

        return all_raw

    def _map_voucher(self, item: dict[str, Any]) -> RawOffer | None:
        """Map Awin voucher object to RawOffer."""
        try:
            # Awin voucher fields: title, description, voucherCode, startDate, endDate,
            # advertiserName, advertiserId, deepLink, discountAmount, discountPercentage,
            # discountType, currency, categories, regions
            title = item.get("title") or item.get("description") or "Awin Voucher"
            code = item.get("voucherCode")
            if not code:
                return None  # skip vouchers without codes

            destination = item.get("deepLink") or item.get("trackingUrl")
            if not destination:
                return None

            discount_type = item.get("discountType")
            discount_value = item.get("discountAmount") or item.get("discountPercentage")
            discount_text = None
            if discount_type == "percentage" and discount_value:
                discount_text = f"{discount_value}% off"
            elif discount_type == "fixed" and discount_value:
                discount_text = f"{discount_value} off"

            return RawOffer(
                title=title,
                code=code.strip().upper(),
                description=item.get("description"),
                discount_type=discount_type,
                discount_value=float(discount_value) if discount_value else None,
                discount_text=discount_text,
                destination_url=destination,
                expires_at=item.get("endDate"),
                external_id=str(item.get("id")) if item.get("id") else None,
                store_slug=item.get("advertiserName", "").lower().replace(" ", "-"),
                store_name=item.get("advertiserName"),
                category_slug=None,  # Awin categories don't map directly
                store_website=item.get("advertiserUrl"),
            )
        except (KeyError, ValueError, TypeError):
            # Log and skip bad items rather than failing the whole fetch
            return None

    def _map_deal(self, item: dict[str, Any]) -> RawOffer | None:
        """Map Awin deal object to RawOffer."""
        try:
            title = item.get("title") or item.get("description") or "Awin Deal"
            destination = item.get("deepLink") or item.get("trackingUrl")
            if not destination:
                return None

            discount_type = item.get("dealType")  # "sale", "offer", etc.
            discount_value = item.get("discountAmount") or item.get("discountPercentage")
            discount_text = None
            if discount_type == "percentage" and discount_value:
                discount_text = f"{discount_value}% off"
            elif discount_type == "fixed" and discount_value:
                discount_text = f"{discount_value} off"

            return RawOffer(
                title=title,
                code=None,  # deals don't have codes
                description=item.get("description"),
                discount_type=discount_type,
                discount_value=float(discount_value) if discount_value else None,
                discount_text=discount_text,
                destination_url=destination,
                expires_at=item.get("endDate"),
                external_id=str(item.get("id")) if item.get("id") else None,
                store_slug=item.get("advertiserName", "").lower().replace(" ", "-"),
                store_name=item.get("advertiserName"),
                category_slug=None,
                store_website=item.get("advertiserUrl"),
            )
        except (KeyError, ValueError, TypeError):
            return None