"""What an external source hands us, before normalization."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


@dataclass
class RawOffer:
    title: str
    store_slug: str | None = None
    store_name: str | None = None
    category_slug: str | None = None
    category_name: str | None = None
    code: str | None = None
    description: str | None = None
    discount_type: str | None = None  # "percentage" | "fixed" | "deal"
    discount_value: float | None = None
    discount_text: str | None = None  # e.g. "50% off", "₹200 off", "BOGO"
    destination_url: str = ""
    expires_at: datetime | str | None = None
    external_id: str | None = None
    store_website: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class SourceAdapter(Protocol):
    """Turns a Source's config into raw offers. One method, async, no DB access."""

    async def fetch(self, config: dict[str, Any]) -> list[RawOffer]: ...
