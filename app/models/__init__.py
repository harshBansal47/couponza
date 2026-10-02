"""Import every model so SQLAlchemy's registry is complete wherever `app.models` is used."""

from app.models.ad import Ad
from app.models.category import Category
from app.models.coupon import Coupon
from app.models.coupon_verification import CouponVerification
from app.models.ingestion_run import IngestionRun
from app.models.page import Page
from app.models.product import PricePoint, Product
from app.models.source import Source
from app.models.store import Store
from app.models.tracking import (
    AlertEvent,
    NotificationPreference,
    SavedCoupon,
    SavedStore,
    TrackAlertState,
    TrackedProduct,
)
from app.models.user import User

__all__ = [
    "Ad",
    "AlertEvent",
    "Category",
    "Coupon",
    "CouponVerification",
    "IngestionRun",
    "NotificationPreference",
    "Page",
    "PricePoint",
    "Product",
    "SavedCoupon",
    "SavedStore",
    "Source",
    "Store",
    "TrackAlertState",
    "TrackedProduct",
    "User",
]
