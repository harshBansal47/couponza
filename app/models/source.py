import enum

from sqlalchemy import JSON, Boolean, Enum, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPKMixin


class SourceKind(str, enum.Enum):
    """Which adapter turns this source's config into RawOffers."""

    csv = "csv"
    static = "static"  # inline offer list in config — used for tests and seed data
    feed = "feed"  # JSON over HTTP — the path to real automated sources


class Source(Base, UUIDPKMixin, TimestampMixin):
    """A place coupons come from. `config` is adapter-specific JSON.

    csv:    {"path": "/data/offers.csv", "default_category_slug": "general"}
    static: {"offers": [{...RawOffer fields...}]}
    feed:   {"url": "https://example.com/offers.json"}  # a JSON list, or {"offers": [...]}
    """

    __tablename__ = "sources"

    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(140), unique=True, index=True, nullable=False)
    kind: Mapped[SourceKind] = mapped_column(
        Enum(SourceKind, name="source_kind", native_enum=True), nullable=False
    )
    config: Mapped[dict] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False, default=dict
    )
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # When true, the pipeline issues a live HTTP probe of each offer's
    # destination_url and marks unreachable offers `failed`.
    probe_destinations: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __str__(self) -> str:
        return self.name
