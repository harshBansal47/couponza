"""Turn messy source data into canonical form before it touches the DB."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from slugify import slugify

from app.ingestion.base import RawOffer
from app.models.coupon import DiscountType

_EXPIRY_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%d %b %Y", "%d %B %Y")


class NormalizeError(ValueError):
    pass


@dataclass
class NormalizedOffer:
    title: str
    code: str | None
    description: str | None
    discount_type: DiscountType
    discount_value: float | None
    store_slug: str
    store_name: str
    category_slug: str | None
    category_name: str | None
    destination_url: str
    expires_at: datetime | None
    external_id: str | None
    store_website: str | None


def _parse_expires(value: datetime | str | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    text = value.strip()
    relative = re.fullmatch(r"in (\d+) days?", text, flags=re.IGNORECASE)
    if relative:
        return datetime.now(UTC) + timedelta(days=int(relative.group(1)))
    try:
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        pass
    for fmt in _EXPIRY_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    raise NormalizeError(f"unparseable expires_at: {value!r}")


def parse_discount(discount_text: str | None) -> tuple[DiscountType, float | None] | None:
    if not discount_text:
        return None
    text = discount_text.strip().lower()
    if any(word in text for word in ("bogo", "buy one", "deal", "free shipping", "special offer")):
        return DiscountType.deal, None
    percent = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
    if percent:
        return DiscountType.percentage, float(percent.group(1))
    amount = re.search(r"(?:rs\.?|inr|₹|\$|usd)?\s*(\d+(?:\.\d+)?)\s*(?:off|discount)", text)
    if amount:
        return DiscountType.fixed, float(amount.group(1))
    return None


def normalize_offer(raw: RawOffer) -> NormalizedOffer:
    title = " ".join(raw.title.split()).strip()
    if not title:
        raise NormalizeError("empty title")
    if len(title) > 200:
        raise NormalizeError(f"title too long ({len(title)} chars)")

    code = raw.code.strip().upper() if raw.code and raw.code.strip() else None

    destination_url = raw.destination_url.strip()
    if not destination_url:
        raise NormalizeError("missing destination_url")

    discount_type: DiscountType | None = None
    if raw.discount_type:
        try:
            discount_type = DiscountType(raw.discount_type.strip().lower())
        except ValueError as exc:
            raise NormalizeError(f"bad discount_type: {raw.discount_type!r}") from exc
    discount_value = raw.discount_value
    if discount_type is None:
        parsed = parse_discount(raw.discount_text)
        if parsed is not None:
            discount_type, discount_value = parsed
    if discount_type is None:
        discount_type = DiscountType.deal  # no parseable discount info -> treat as deal

    store_slug = raw.store_slug or (slugify(raw.store_name) if raw.store_name else "")
    if not store_slug:
        raise NormalizeError("offer has no store_slug/store_name")
    store_name = raw.store_name or (
        raw.store_slug.replace("-", " ").title() if raw.store_slug else ""
    )

    category_slug = raw.category_slug or (slugify(raw.category_name) if raw.category_name else None)

    return NormalizedOffer(
        title=title,
        code=code,
        description=raw.description.strip() if raw.description else None,
        discount_type=discount_type,
        discount_value=discount_value,
        store_slug=store_slug,
        store_name=store_name,
        category_slug=category_slug or None,
        category_name=raw.category_name,
        destination_url=destination_url,
        expires_at=_parse_expires(raw.expires_at),
        external_id=(raw.external_id or None),
        store_website=raw.store_website,
    )


def offer_from_dict(data: dict[str, Any]) -> RawOffer:
    """Build a RawOffer from a plain dict (CSV row, static config entry, API body)."""
    known = {f for f in RawOffer.__dataclass_fields__ if f != "extra"}
    return RawOffer(**{k: v for k, v in data.items() if k in known})
