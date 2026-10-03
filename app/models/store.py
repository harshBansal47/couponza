from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPKMixin


class Store(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "stores"

    name: Mapped[str] = mapped_column(String(150), nullable=False)
    slug: Mapped[str] = mapped_column(String(170), unique=True, index=True, nullable=False)
    logo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    website_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Optional per-store note, e.g. "We earn a commission from Amazon on this link."
    # Falls back to a sitewide default in the frontend when null — see the Trust page.
    commission_disclosure: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Affiliate wiring. `affiliate_network` picks how the click reference is
    # attached to outbound links; `link_template` overrides it for programmes
    # that need a deeplink wrapper (see app/services/affiliate.py). Never
    # exposed by the public API.
    affiliate_network: Mapped[str] = mapped_column(
        String(20), default="none", server_default="none", nullable=False
    )
    link_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Informational: how long the network credits a click, for revenue analysis.
    cookie_days: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Market data. Couponbase lists stores per country, so a store carries the
    # market it belongs to: ISO 3166-1 alpha-2 for routing/sitemaps, ISO 4217
    # for rendering prices. Both nullable — a global store (Amazon) may not
    # belong to one market.
    country_code: Mapped[str | None] = mapped_column(String(2), index=True, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)

    def __str__(self) -> str:
        return self.name
