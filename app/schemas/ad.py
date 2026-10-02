import uuid
from datetime import datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict

from app.models.ad import AdPosition


class AdCreate(BaseModel):
    position: AdPosition
    image_url: str
    target_url: str
    starts_at: AwareDatetime | None = None
    ends_at: AwareDatetime | None = None


class AdUpdate(BaseModel):
    position: AdPosition | None = None
    image_url: str | None = None
    target_url: str | None = None
    starts_at: AwareDatetime | None = None
    ends_at: AwareDatetime | None = None
    is_active: bool | None = None


class AdRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    position: AdPosition
    image_url: str
    target_url: str
    starts_at: datetime | None
    ends_at: datetime | None
    is_active: bool
    created_at: datetime
