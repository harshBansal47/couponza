"""Import every model so SQLAlchemy's registry is complete wherever `app.models` is used."""

from app.models.ad import Ad
from app.models.category import Category
from app.models.click import ClickEvent
from app.models.conversion import Conversion, ConversionStatus
from app.models.coupon import Coupon
from app.models.coupon_verification import CouponVerification
from app.models.ingestion_run import IngestionRun
from app.models.job import JobRun, JobStatus
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
from app.models.verification_attempt import AttemptOutcome, VerificationAttempt

__all__ = [
    "Ad",
    "AlertEvent",
    "AttemptOutcome",
    "Category",
    "ClickEvent",
    "Conversion",
    "ConversionStatus",
    "Coupon",
    "CouponVerification",
    "IngestionRun",
    "JobRun",
    "JobStatus",
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
    "VerificationAttempt",
]
