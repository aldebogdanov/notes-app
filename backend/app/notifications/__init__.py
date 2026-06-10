"""Notification channels: shared constants and helpers.

M1 ships only the schema-level pieces. The adapter registry replaces
KNOWN_CHANNELS in M2 under the same import path.
"""

import enum
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models import Note

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
