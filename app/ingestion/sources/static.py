from typing import Any

from app.ingestion.base import RawOffer
from app.ingestion.normalize import offer_from_dict


class StaticSource:
    """Offers embedded directly in the Source's config — tests, seed data, demos."""

    async def fetch(self, config: dict[str, Any]) -> list[RawOffer]:
        return [offer_from_dict(entry) for entry in config.get("offers", [])]
