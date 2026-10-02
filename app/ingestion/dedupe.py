"""Dedup identity: the same offer arriving twice (or from two sources) maps to one coupon."""

import hashlib

from app.ingestion.normalize import NormalizedOffer


def content_hash(offer: NormalizedOffer) -> str:
    # Identity is (store, code-or-title) — NOT the discount value, so a changed
    # discount updates the existing coupon instead of spawning a duplicate.
    code_or_title = offer.code or offer.title.lower()
    key = f"{offer.store_slug}|{code_or_title}"
    return hashlib.sha256(key.encode()).hexdigest()
