import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class StoreCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    logo_url: str | None = None
    website_url: str | None = None
    description: str | None = None
    commission_disclosure: str | None = None


class StoreUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    logo_url: str | None = None
    website_url: str | None = None
    description: str | None = None
    is_active: bool | None = None
    commission_disclosure: str | None = None


class StoreRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    logo_url: str | None
    website_url: str | None
    description: str | None
    is_active: bool
    commission_disclosure: str | None
    created_at: datetime
