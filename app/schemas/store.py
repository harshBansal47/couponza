import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _validate_country(value: str | None) -> str | None:
    if value is None:
        return None
    code = value.strip().upper()
    if len(code) != 2 or not code.isalpha():
        raise ValueError("country_code must be a 2-letter ISO 3166-1 alpha-2 code")
    return code


def _validate_currency(value: str | None) -> str | None:
    if value is None:
        return None
    code = value.strip().upper()
    if len(code) != 3 or not code.isalpha():
        raise ValueError("currency must be a 3-letter ISO 4217 code")
    return code


class StoreCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    logo_url: str | None = None
    website_url: str | None = None
    description: str | None = None
    commission_disclosure: str | None = None
    country_code: str | None = None
    currency: str | None = None

    _check_country = field_validator("country_code")(_validate_country)
    _check_currency = field_validator("currency")(_validate_currency)


class StoreUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    logo_url: str | None = None
    website_url: str | None = None
    description: str | None = None
    is_active: bool | None = None
    commission_disclosure: str | None = None
    country_code: str | None = None
    currency: str | None = None

    _check_country = field_validator("country_code")(_validate_country)
    _check_currency = field_validator("currency")(_validate_currency)


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
    country_code: str | None
    currency: str | None
    created_at: datetime
