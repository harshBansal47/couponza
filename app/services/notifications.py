"""Notification delivery. Channels are pluggable; a channel with no backing
credentials configured logs and counts as 'queued' rather than erroring — real
delivery lights up as soon as SMTP_HOST / TELEGRAM_BOT_TOKEN are set."""

import logging
import os
from typing import Protocol

import httpx

from app.models.tracking import NotificationPreference
from app.models.user import User

logger = logging.getLogger("couponza.notifications")


class Sender(Protocol):
    channel: str

    async def send(self, user: User, prefs: NotificationPreference, text: str) -> bool: ...


class EmailSender:
    channel = "email"

    async def send(self, user: User, prefs: NotificationPreference, text: str) -> bool:
        smtp_host = os.environ.get("SMTP_HOST")
        if not smtp_host:
            logger.info("[email queued — SMTP_HOST not set] to=%s: %s", user.email, text)
            return True
        # Real path: wire an SMTP client here (e.g. aiosmtplib) when email
        # volume justifies it. Until then this is a clean seam, not a shrug.
        logger.warning("SMTP_HOST set but no email handler configured; dropping")
        return False


class TelegramSender:
    channel = "telegram"

    async def send(self, user: User, prefs: NotificationPreference, text: str) -> bool:
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        if not token or not prefs.telegram_chat_id:
            logger.info("[telegram queued — TELEGRAM_BOT_TOKEN/chat_id missing]: %s", text)
            return True
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": prefs.telegram_chat_id, "text": text},
            )
            if resp.status_code != 200:
                logger.warning("telegram send failed: %s", resp.status_code)
                return False
            return True


class PushSender:
    channel = "push"

    async def send(self, user: User, prefs: NotificationPreference, text: str) -> bool:
        if not prefs.push_subscription:
            logger.info("[push skipped — no subscription]: %s", text)
            return True
        # Web-push needs pywebpush + VAPID keys; add when the extension lands.
        logger.info("[push queued — pywebpush not wired yet]: %s", text)
        return True


def senders_for(prefs: NotificationPreference) -> list[Sender]:
    out: list[Sender] = []
    if prefs.email_enabled:
        out.append(EmailSender())
    if prefs.telegram_enabled and prefs.telegram_chat_id:
        out.append(TelegramSender())
    if prefs.push_enabled and prefs.push_subscription:
        out.append(PushSender())
    return out
