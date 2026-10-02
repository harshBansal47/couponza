import csv
from pathlib import Path
from typing import Any

from app.ingestion.base import RawOffer
from app.ingestion.normalize import offer_from_dict


class CsvSource:
    """Reads offers from a CSV file. Column headers map to RawOffer fields.

    Expected columns (subset is fine): title, store_slug, store_name, category_slug,
    category_name, code, description, discount_type, discount_value, discount_text,
    destination_url, expires_at, external_id.
    """

    async def fetch(self, config: dict[str, Any]) -> list[RawOffer]:
        path = Path(config["path"])
        if not path.exists():
            raise FileNotFoundError(f"CSV source not found: {path}")
        # Small, local CSV files — the blocking read is fine here; noqa for ASYNC230.
        with path.open(newline="", encoding="utf-8-sig") as fh:  # noqa: ASYNC230
            rows = list(csv.DictReader(fh))
        offers: list[RawOffer] = []
        for row in rows:
            cleaned = {k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in row.items()}
            if cleaned.get("discount_value"):
                try:
                    cleaned["discount_value"] = float(cleaned["discount_value"])
                except ValueError:
                    pass
            offers.append(offer_from_dict(cleaned))
        return offers
