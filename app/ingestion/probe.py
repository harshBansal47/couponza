"""Live check that a destination URL still resolves. Off by default: probing
every offer is slow and some sites block bots, so sources opt in via
`probe_destinations`."""

import httpx

PROBE_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
_USER_AGENT = "CouponzaBot/1.0 (+https://couponza.example.com/bot)"


async def probe_destination(url: str) -> tuple[bool, str | None]:
    """Return (ok, failure_reason). ok=True means the URL gave a 2xx/3xx."""
    try:
        async with httpx.AsyncClient(
            timeout=PROBE_TIMEOUT, follow_redirects=True, headers={"User-Agent": _USER_AGENT}
        ) as client:
            response = await client.get(url)
            if response.status_code < 400:
                return True, None
            return False, f"destination returned HTTP {response.status_code}"
    except httpx.TimeoutException:
        return False, "destination probe timed out"
    except httpx.HTTPError as exc:
        return False, f"destination probe failed: {type(exc).__name__}"
