"""The view anonymous visitors (and AI agents) get: no destination_url.

The real URL is only ever resolved server-side by the /go/{id} redirect, so a
visitor — human or agent — can browse and decide, but can't scrape the raw
affiliate link straight out of the API response.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, computed_field

from app.models.coupon import DiscountType


def compute_success_rate(success_count: int, fail_count: int) -> float | None:
    total = success_count + fail_count
    return round(success_count / total, 3) if total else None


class CouponPublicRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    slug: str
    code: str | None
    description: str | None
    discount_type: DiscountType
    discount_value: float | None
    store_id: uuid.UUID
    category_id: uuid.UUID
    expires_at: datetime | None
    is_active: bool
    views_count: int
    clicks_count: int
    success_count: int
    fail_count: int
    last_verified_at: datetime | None
    created_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def success_rate(self) -> float | None:
        return compute_success_rate(self.success_count, self.fail_count)


class VerifyRequest(BaseModel):
    worked: bool


class VerifyResponse(BaseModel):
    success_count: int
    fail_count: int
    last_verified_at: datetime | None
    success_rate: float | None


class VerificationHistoryItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    worked: bool
    created_at: datetime
    note: str | None = None
