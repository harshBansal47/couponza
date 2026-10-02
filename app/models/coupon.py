import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.category import Category
    from app.models.store import Store


class DiscountType(str, enum.Enum):
    percentage = "percentage"
    fixed = "fixed"
    deal = "deal"


class CouponStatus(str, enum.Enum):
    """Lifecycle state maintained by the ingestion/verification pipeline.

    `is_active` is kept in sync (active <-> True) because the public API's
    existing `active_only` filters rely on it.
    """

    active = "active"
    failed = "failed"
    expired = "expired"


class Coupon(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "coupons"

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(220), unique=True, index=True, nullable=False)
    # Nullable: "deals" without a code leave this empty.
    code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    discount_type: Mapped[DiscountType] = mapped_column(
        Enum(DiscountType, name="discount_type", native_enum=True), nullable=False
    )
    discount_value: Mapped[float | None] = mapped_column(
        Numeric(10, 2, asdecimal=False), nullable=True
    )
    store_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("stores.id", ondelete="CASCADE"), index=True, nullable=False
    )
    category_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("categories.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    # The real affiliate URL. Phase 5 adds the /go/{id} redirect so visitors never see it.
    destination_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    views_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    clicks_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Community verification ("worked for me" / "didn't work"), kept denormalized on the
    # coupon for fast reads; CouponVerification rows are the source of truth + audit trail.
    success_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    fail_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Ingestion pipeline bookkeeping. Manually-created coupons (admin/API) leave
    # these null and are treated as `active` until the lifecycle sweep expires them.
    status: Mapped[CouponStatus] = mapped_column(
        Enum(CouponStatus, name="coupon_status", native_enum=True),
        default=CouponStatus.active,
        nullable=False,
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sources.id", ondelete="SET NULL"), index=True, nullable=True
    )
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # sha256 of (store, code, discount) — the dedup identity across sources.
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)

    # Read-side conveniences (the admin panel's dropdowns). The JSON API doesn't load them.
    store: Mapped["Store"] = relationship()
    category: Mapped["Category"] = relationship()

    def __str__(self) -> str:
        return self.title
