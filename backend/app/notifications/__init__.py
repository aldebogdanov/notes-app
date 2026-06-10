"""Notification channels: adapters, registry, shared constants and helpers.

KNOWN_CHANNELS = channels the product implements (drives "pending" derivation
even on deployments without a token). The adapter registry is the subset
actually configured at runtime — see registry.py.
"""

import enum
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from .base import NotificationAdapter, NotificationSendError
from .registry import build_adapter_registry

if TYPE_CHECKING:
    from ..models import Note, User

__all__ = [
    "KNOWN_CHANNELS",
    "ChannelConfig",
    "NotificationAdapter",
    "NotificationSendError",
    "NotificationStatus",
    "build_adapter_registry",
    "get_channel_config",
    "notification_status_map",
    "user_timezone",
]

KNOWN_CHANNELS: tuple[str, ...] = ("telegram",)


class NotificationStatus(enum.StrEnum):
    PENDING = "pending"
    SENT = "sent"
    SKIPPED = "skipped"
    FAILED = "failed"


def notification_status_map(note: "Note", today: date) -> dict[str, str]:
    """Per-channel notification status for a note.

    A persisted row wins; a known channel without a row reads as "pending"
    while the note's date is today or in the future. Notes without a date
    (or stale past dates that predate backfill) get no key for that channel.
    """
    out: dict[str, str] = {row.channel: row.status for row in note.notifications}
    if note.note_date is not None and note.note_date >= today:
        for channel in KNOWN_CHANNELS:
            out.setdefault(channel, NotificationStatus.PENDING)
    return out


@dataclass(frozen=True)
class ChannelConfig:
    enabled: bool = False
    chat_ref: str | None = None


def get_channel_config(user: "User", channel: str) -> ChannelConfig:
    """Single reader for the notification_settings JSON shape (M4 reuses it)."""
    channels = (user.notification_settings or {}).get("channels") or {}
    config = channels.get(channel) or {}
    chat_id = config.get("chat_id")
    return ChannelConfig(
        enabled=bool(config.get("enabled")),
        chat_ref=str(chat_id) if chat_id is not None else None,
    )


def user_timezone(user: "User") -> ZoneInfo:
    """Owner's IANA timezone; anything invalid falls back to UTC, never raises."""
    name = (user.notification_settings or {}).get("timezone") or "UTC"
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("UTC")
