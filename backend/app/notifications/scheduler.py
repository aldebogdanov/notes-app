import asyncio
import contextlib
import logging
from collections.abc import Callable
from datetime import UTC, datetime, time

from sqlalchemy.orm import joinedload, selectinload

from ..models import Note, NoteNotification, User
from . import (
    KNOWN_CHANNELS,
    NotificationAdapter,
    NotificationSendError,
    NotificationStatus,
    get_channel_config,
    user_timezone,
)

logger = logging.getLogger(__name__)


def compose_reminder(note: Note) -> str:
    if note.content:
        return f"🔔 {note.title}\n\n{note.content}"
    return f"🔔 {note.title}"


def _aware_utc(value: datetime) -> datetime:
    # SQLite test fixtures return naive datetimes; the app stores UTC.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _due_moment(note: Note, owner: User) -> datetime:
    return datetime.combine(note.note_date, time.min, tzinfo=user_timezone(owner))


class ReminderScheduler:
    def __init__(
        self,
        session_factory,
        registry: dict[str, NotificationAdapter],
        *,
        now: Callable[[], datetime] | None = None,
        rescan_interval: float = 900.0,
        max_attempts: int = 3,
    ) -> None:
        self._session_factory = session_factory
        self._registry = registry
        self._now = now or (lambda: datetime.now(UTC))
        self._rescan_interval = rescan_interval
        self._max_attempts = max_attempts
        self._wake = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None

    def notify_change(self) -> None:
        """Wake the loop. Thread-safe: note CRUD runs in FastAPI's threadpool."""
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(self._wake.set)

    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        while True:
            try:
                await self.process_due_notes()
            except Exception:
                logger.exception("reminder pass failed; loop continues")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=self._next_wait_seconds())
            self._wake.clear()

    async def process_due_notes(self) -> None:
        session = self._session_factory()
        try:
            now = self._now()
            notes = (
                session.query(Note)
                .options(selectinload(Note.notifications), joinedload(Note.owner))
                .filter(Note.note_date.is_not(None))
                .all()
            )
            for note in notes:
                due_at = _due_moment(note, note.owner)
                if due_at > now:
                    continue
                rows = {r.channel: r for r in note.notifications}
                for channel in KNOWN_CHANNELS:
                    row = rows.get(channel)
                    if row is not None and (
                        row.status != NotificationStatus.PENDING
                        or row.attempts >= self._max_attempts
                    ):
                        continue
                    await self._process_one(session, note, channel, row, due_at, now)
        finally:
            session.close()

    async def _process_one(
        self,
        session,
        note: Note,
        channel: str,
        row: NoteNotification | None,
        due_at: datetime,
        now: datetime,
    ) -> None:
        def ensure_row() -> NoteNotification:
            nonlocal row
            if row is None:
                row = NoteNotification(note_id=note.id, channel=channel, attempts=0)
                session.add(row)
            return row

        config = get_channel_config(note.owner, channel)
        adapter = self._registry.get(channel)
        born_past_due = _aware_utc(note.created_at) > due_at
        deliverable = adapter is not None and config.enabled and config.chat_ref is not None
        if note.archived_at is not None or born_past_due or not deliverable:
            ensure_row().status = NotificationStatus.SKIPPED
            session.commit()
            return

        # Claim before sending: a crash after send but before finalize means
        # at-least-once delivery (documented), never a silent loss.
        claimed = ensure_row()
        claimed.status = NotificationStatus.PENDING
        claimed.attempts += 1
        session.commit()
        try:
            await adapter.send(config.chat_ref, compose_reminder(note))
        except NotificationSendError as exc:
            claimed.last_error = str(exc)
            if claimed.attempts >= self._max_attempts:
                claimed.status = NotificationStatus.FAILED
            session.commit()
            return
        claimed.status = NotificationStatus.SENT
        claimed.sent_at = now
        session.commit()

    def _next_wait_seconds(self) -> float:
        try:
            next_due = self._next_due_moment()
        except Exception:
            logger.exception("next-wake computation failed; falling back to rescan")
            return self._rescan_interval
        if next_due is None:
            return self._rescan_interval
        delta = (next_due - self._now()).total_seconds()
        return min(delta, self._rescan_interval)

    def _next_due_moment(self) -> datetime | None:
        """Earliest *upcoming* due instant among dated, non-archived notes.

        Past moments are excluded — the pass just resolved them (pending
        retries ride the safety rescan). Terminal-state filtering is skipped
        on purpose: a spurious wake-up is a cheap no-op pass, while the
        filter would need per-channel SQL.
        """
        session = self._session_factory()
        try:
            # No DISTINCT: Postgres json columns have no equality operator;
            # duplicate (date, settings) pairs are deduped by min() anyway.
            pairs = (
                session.query(Note.note_date, User.notification_settings)
                .join(User, Note.user_id == User.id)
                .filter(Note.note_date.is_not(None), Note.archived_at.is_(None))
                .all()
            )
        finally:
            session.close()
        now = self._now()
        moments = []
        for note_date, settings_json in pairs:
            owner = User(notification_settings=settings_json or {})
            moment = datetime.combine(note_date, time.min, tzinfo=user_timezone(owner))
            if moment > now:
                moments.append(moment)
        return min(moments, default=None)
