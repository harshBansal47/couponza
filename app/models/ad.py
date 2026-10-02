import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPKMixin


class AdPosition(str, enum.Enum):
    header = "header"
    sidebar = "sidebar"
    footer = "footer"


class Ad(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "ads"

    position: Mapped[AdPosition] = mapped_column(
        Enum(AdPosition, name="ad_position", native_enum=True), nullable=False
    )
    image_url: Mapped[str] = mapped_column(String(500), nullable=False)
    target_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
