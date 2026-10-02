import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.tracking import AlertKind


class SavedItemRead(BaseModel):
    kind: str  # "store" | "coupon"
    item_id: uuid.UUID
    saved_id: uuid.UUID


class TrackedProductCreate(BaseModel):
    product_id: uuid.UUID
    target_price: float | None = Field(default=None, gt=0)


class TrackedProductUpdate(BaseModel):
    target_price: float | None = Field(default=None, gt=0)


class TrackedProductRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    product_id: uuid.UUID
    target_price: float | None
    created_at: datetime


class NotificationPreferenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    email_enabled: bool
    telegram_enabled: bool
    telegram_chat_id: str | None
    push_enabled: bool
    push_subscription: str | None


class NotificationPreferenceUpdate(BaseModel):
    email_enabled: bool | None = None
    telegram_enabled: bool | None = None
    telegram_chat_id: str | None = None
    push_enabled: bool | None = None
    push_subscription: str | None = None


class AlertEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: AlertKind
    channel: str
    detail: str | None
    price_point_id: uuid.UUID | None
    created_at: datetime
