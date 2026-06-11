import asyncio
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Note, NoteNotification, User
from app.notifications import NotificationSendError
from app.notifications.scheduler import ReminderScheduler

NOW = datetime(2026, 6, 10, 12, 0, tzinfo=UTC)
TODAY = date(2026, 6, 10)
TOMORROW = date(2026, 6, 11)
YESTERDAY = date(2026, 6, 9)
LONG_AGO = datetime(2026, 6, 1, 12, 0)  # naive UTC, like SQLite returns

ENABLED = {"timezone": "UTC", "channels": {"telegram": {"enabled": True, "chat_id": 777}}}


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FakeClock:
    def __init__(self, current: datetime = NOW):
        self.current = current

    def __call__(self) -> datetime:
        return self.current


class FakeAdapter:
    name = "telegram"

    def __init__(self):
        self.sent: list[tuple[str, str]] = []
        self.failures: list[Exception] = []

    async def send(self, chat_ref: str, text: str) -> None:
        if self.failures:
            raise self.failures.pop(0)
        self.sent.append((chat_ref, text))


@pytest.fixture
def session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'sched.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    yield factory
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def adapter():
    return FakeAdapter()


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def scheduler(session_factory, adapter, clock):
    return ReminderScheduler(session_factory, {"telegram": adapter}, now=clock, max_attempts=3)


def make_user(session, settings=ENABLED, username="u1"):
    user = User(username=username, password_hash="x", notification_settings=settings)
    session.add(user)
    session.flush()
    return user


def make_note(session, user, note_date, *, created_at=LONG_AGO, archived=False, title="n"):
    note = Note(
        user_id=user.id,
        title=title,
        content="body",
        note_date=note_date,
        created_at=created_at,
        archived_at=NOW if archived else None,
    )
    session.add(note)
    session.commit()
    return note


def get_row(session, note_id, channel="telegram"):
    return session.query(NoteNotification).filter_by(note_id=note_id, channel=channel).one_or_none()


# --- pass-level ---


@pytest.mark.anyio
async def test_due_today_enabled_linked_sends(scheduler, session_factory, adapter):
    s = session_factory()
    note = make_note(s, make_user(s), TODAY, title="Standup")

    await scheduler.process_due_notes()

    assert adapter.sent == [("777", "🔔 Standup\n\nbody")]
    row = get_row(s, note.id)
    assert row.status == "sent"
    assert row.sent_at is not None
    s.close()


@pytest.mark.anyio
async def test_catchup_sent_but_born_past_due_skipped(scheduler, session_factory, adapter):
    s = session_factory()
    user = make_user(s)
    catchup = make_note(s, user, YESTERDAY, created_at=LONG_AGO)
    born_late = make_note(s, user, YESTERDAY, created_at=datetime(2026, 6, 10, 11, 0))

    await scheduler.process_due_notes()

    assert get_row(s, catchup.id).status == "sent"
    assert get_row(s, born_late.id).status == "skipped"
    assert len(adapter.sent) == 1
    s.close()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "settings",
    [
        {"channels": {"telegram": {"enabled": False, "chat_id": 777}}},  # disabled
        {"channels": {"telegram": {"enabled": True, "chat_id": None}}},  # unlinked
        {},  # nothing configured
    ],
)
async def test_undeliverable_day_over_skipped_but_today_stays_live(
    session_factory, adapter, clock, settings
):
    scheduler = ReminderScheduler(session_factory, {"telegram": adapter}, now=clock)
    s = session_factory()
    user = make_user(s, settings)
    yesterday_note = make_note(s, user, YESTERDAY, title="day over")
    today_note = make_note(s, user, TODAY, title="still live")

    await scheduler.process_due_notes()

    # The finished day is a terminal miss…
    assert get_row(s, yesterday_note.id).status == "skipped"
    # …but today's reminder window is still open: no row, stays pending.
    assert get_row(s, today_note.id) is None
    assert adapter.sent == []
    s.close()


@pytest.mark.anyio
async def test_adapter_not_in_registry_skips_after_day_over(session_factory, clock):
    scheduler = ReminderScheduler(session_factory, {}, now=clock)
    s = session_factory()
    note = make_note(s, make_user(s), YESTERDAY)

    await scheduler.process_due_notes()

    assert get_row(s, note.id).status == "skipped"
    s.close()


@pytest.mark.anyio
async def test_archived_note_skipped_after_day_over_live_today(scheduler, session_factory, adapter):
    s = session_factory()
    user = make_user(s)
    old_archived = make_note(s, user, YESTERDAY, archived=True, title="old")
    today_archived = make_note(s, user, TODAY, archived=True, title="today")

    await scheduler.process_due_notes()

    assert get_row(s, old_archived.id).status == "skipped"
    # Unarchiving later today should still deliver — no terminal row yet.
    assert get_row(s, today_archived.id) is None
    assert adapter.sent == []
    s.close()


@pytest.mark.anyio
async def test_enabling_later_the_same_day_delivers(session_factory, adapter, clock):
    # The reported scenario: a pass runs while the user is still configuring
    # (disabled), then the user links+enables — today's note must still fire.
    scheduler = ReminderScheduler(session_factory, {"telegram": adapter}, now=clock)
    s = session_factory()
    user = make_user(s, {"channels": {"telegram": {"enabled": False, "chat_id": None}}})
    note = make_note(s, user, TODAY)

    await scheduler.process_due_notes()
    assert get_row(s, note.id) is None
    assert adapter.sent == []

    user.notification_settings = {
        "timezone": "UTC",
        "channels": {"telegram": {"enabled": True, "chat_id": 777}},
    }
    s.commit()

    await scheduler.process_due_notes()
    assert get_row(s, note.id).status == "sent"
    assert len(adapter.sent) == 1
    s.close()


@pytest.mark.anyio
async def test_future_note_untouched(scheduler, session_factory, adapter):
    s = session_factory()
    note = make_note(s, make_user(s), TOMORROW)

    await scheduler.process_due_notes()

    assert get_row(s, note.id) is None
    assert adapter.sent == []
    s.close()


@pytest.mark.anyio
async def test_terminal_rows_never_reprocessed(scheduler, session_factory, adapter):
    s = session_factory()
    user = make_user(s)
    for i, status in enumerate(["sent", "skipped", "failed"]):
        note = make_note(s, user, TODAY, title=f"n{i}")
        s.add(NoteNotification(note_id=note.id, channel="telegram", status=status))
    s.commit()

    await scheduler.process_due_notes()

    assert adapter.sent == []
    assert s.query(NoteNotification).count() == 3
    s.close()


@pytest.mark.anyio
async def test_failures_then_recovery_and_exhaustion(scheduler, session_factory, adapter):
    s = session_factory()
    user = make_user(s)
    note = make_note(s, user, TODAY)

    adapter.failures = [NotificationSendError("boom 1")]
    await scheduler.process_due_notes()
    row = get_row(s, note.id)
    s.refresh(row)
    assert (row.status, row.attempts, row.last_error) == ("pending", 1, "boom 1")

    # recovery on a later pass
    await scheduler.process_due_notes()
    s.refresh(row)
    assert (row.status, row.attempts) == ("sent", 2)
    assert len(adapter.sent) == 1

    # exhaustion path on a fresh note
    note2 = make_note(s, user, TODAY, title="doomed")
    adapter.failures = [NotificationSendError(f"boom {i}") for i in range(3)]
    for _ in range(3):
        await scheduler.process_due_notes()
    row2 = get_row(s, note2.id)
    s.refresh(row2)
    assert (row2.status, row2.attempts) == ("failed", 3)
    assert row2.last_error == "boom 2"

    # failed is terminal
    await scheduler.process_due_notes()
    s.refresh(row2)
    assert row2.attempts == 3
    s.close()


@pytest.mark.anyio
async def test_timezone_boundary(session_factory, adapter):
    clock = FakeClock(datetime(2026, 6, 10, 23, 0, tzinfo=UTC))
    scheduler = ReminderScheduler(session_factory, {"telegram": adapter}, now=clock)
    s = session_factory()
    tokyo = make_user(
        s,
        {"timezone": "Asia/Tokyo", "channels": {"telegram": {"enabled": True, "chat_id": 1}}},
        username="tokyo",
    )
    utc = make_user(s, username="utc")
    tokyo_note = make_note(s, tokyo, TOMORROW, title="tokyo")  # local 2026-06-11 08:00
    utc_note = make_note(s, utc, TOMORROW, title="utc")  # local 2026-06-10 23:00

    await scheduler.process_due_notes()

    assert get_row(s, tokyo_note.id).status == "sent"
    assert get_row(s, utc_note.id) is None
    s.close()


@pytest.mark.anyio
async def test_invalid_timezone_falls_back_to_utc(scheduler, session_factory, adapter):
    s = session_factory()
    user = make_user(
        s,
        {"timezone": "Mars/Olympus", "channels": {"telegram": {"enabled": True, "chat_id": 9}}},
    )
    note = make_note(s, user, TODAY)

    await scheduler.process_due_notes()

    assert get_row(s, note.id).status == "sent"
    s.close()


@pytest.mark.anyio
async def test_second_pass_is_idempotent(scheduler, session_factory, adapter):
    s = session_factory()
    make_note(s, make_user(s), TODAY)

    await scheduler.process_due_notes()
    await scheduler.process_due_notes()

    assert len(adapter.sent) == 1
    s.close()


# --- loop-level ---


async def _wait_until(predicate, timeout=2.0):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0)


@pytest.mark.anyio
async def test_notify_change_wakes_loop(scheduler, session_factory, adapter):
    task = asyncio.create_task(scheduler.run())
    try:
        await _wait_until(lambda: scheduler._loop is not None)
        s = session_factory()
        make_note(s, make_user(s), TODAY)
        s.close()
        scheduler.notify_change()
        await _wait_until(lambda: adapter.sent)
        assert len(adapter.sent) == 1
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.anyio
async def test_unexpected_exception_does_not_kill_loop(scheduler, session_factory, adapter):
    task = asyncio.create_task(scheduler.run())
    try:
        await _wait_until(lambda: scheduler._loop is not None)
        s = session_factory()
        user = make_user(s)
        make_note(s, user, TODAY)
        adapter.failures = [RuntimeError("not a NotificationSendError")]
        scheduler.notify_change()
        await _wait_until(lambda: not adapter.failures)
        assert not task.done()  # loop survived the unexpected exception

        make_note(s, user, TODAY, title="after crash")
        s.close()
        scheduler.notify_change()
        await _wait_until(lambda: len(adapter.sent) == 2)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


def test_lifespan_smoke_with_scheduler_enabled(monkeypatch):
    from fastapi.testclient import TestClient

    from app import config
    from app.main import app

    monkeypatch.setattr(config.settings, "scheduler_enabled", True)
    with TestClient(app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        assert app.state.scheduler is not None
    # context exit = clean cancellation; reset state for other tests
    app.state.scheduler = None


# --- next-wake computation ---


def test_next_wait_picks_earliest_midnight_across_timezones(session_factory, adapter):
    clock = FakeClock(datetime(2026, 6, 10, 12, 0, tzinfo=UTC))
    scheduler = ReminderScheduler(
        session_factory, {"telegram": adapter}, now=clock, rescan_interval=900.0
    )
    s = session_factory()
    tokyo = make_user(s, {"timezone": "Asia/Tokyo"}, username="tokyo")
    utc = make_user(s, username="utc")
    make_note(s, tokyo, TOMORROW)  # midnight at 2026-06-10 15:00 UTC (3h away)
    make_note(s, utc, TOMORROW)  # midnight at 2026-06-11 00:00 UTC (12h away)
    s.close()

    assert scheduler._next_wait_seconds() == 900.0  # capped by rescan interval

    scheduler_uncapped = ReminderScheduler(
        session_factory, {"telegram": adapter}, now=clock, rescan_interval=100000.0
    )
    assert scheduler_uncapped._next_wait_seconds() == 3 * 3600


def test_next_wait_without_candidates_is_rescan(session_factory, adapter):
    scheduler = ReminderScheduler(
        session_factory, {"telegram": adapter}, now=FakeClock(), rescan_interval=900.0
    )
    assert scheduler._next_wait_seconds() == 900.0


def test_next_wait_date_prefilter_keeps_utc_minus_12_today(session_factory, adapter):
    # Etc/GMT+12 is UTC-12 (POSIX sign inversion). At 11:00 UTC the local date
    # is still *yesterday*, so today's UTC date has a future local midnight at
    # 12:00 UTC — exactly the case the >= utc_today - 1 bound must not drop.
    clock = FakeClock(datetime(2026, 6, 10, 11, 0, tzinfo=UTC))
    scheduler = ReminderScheduler(
        session_factory, {"telegram": adapter}, now=clock, rescan_interval=100000.0
    )
    s = session_factory()
    west = make_user(s, {"timezone": "Etc/GMT+12"}, username="west")
    make_note(s, west, TODAY)
    s.close()

    assert scheduler._next_wait_seconds() == 3600.0  # 12:00 UTC midnight, 1h away


def test_next_wait_ignores_past_dates_even_in_utc_plus_14(session_factory, adapter):
    # Pacific/Kiritimati (UTC+14): today's UTC date had its local midnight at
    # 10:00 UTC *yesterday* — already past at 12:00 UTC. Old dates and past
    # midnights must both fall back to plain rescan.
    clock = FakeClock(datetime(2026, 6, 10, 12, 0, tzinfo=UTC))
    scheduler = ReminderScheduler(
        session_factory, {"telegram": adapter}, now=clock, rescan_interval=900.0
    )
    s = session_factory()
    east = make_user(s, {"timezone": "Pacific/Kiritimati"}, username="east")
    make_note(s, east, TODAY)
    make_note(s, east, date(2026, 5, 1))
    s.close()

    assert scheduler._next_wait_seconds() == 900.0


# --- pass query prefilter ---


@pytest.mark.anyio
async def test_old_unresolved_note_caught_up_regardless_of_age(scheduler, session_factory, adapter):
    s = session_factory()
    note = make_note(s, make_user(s), date(2025, 1, 1), created_at=datetime(2024, 12, 1, 12, 0))

    await scheduler.process_due_notes()

    assert get_row(s, note.id).status == "sent"
    assert len(adapter.sent) == 1
    s.close()


@pytest.mark.anyio
async def test_resolved_history_not_picked_up_by_pass(
    scheduler, session_factory, adapter, monkeypatch
):
    s = session_factory()
    user = make_user(s)
    for i, status in enumerate(["sent", "skipped", "failed"]):
        old = make_note(s, user, YESTERDAY, title=f"old{i}")
        s.add(NoteNotification(note_id=old.id, channel="telegram", status=status, attempts=3))
    s.commit()
    fresh = make_note(s, user, TODAY, title="fresh")

    seen: list[int] = []
    original = ReminderScheduler._process_one

    async def spy(self, session, note, *args, **kwargs):
        seen.append(note.id)
        return await original(self, session, note, *args, **kwargs)

    monkeypatch.setattr(ReminderScheduler, "_process_one", spy)
    await scheduler.process_due_notes()

    assert seen == [fresh.id]  # resolved history filtered out in SQL
    s.close()


@pytest.mark.anyio
async def test_note_created_today_for_today_sends(scheduler, session_factory, adapter):
    # The live-demo path: user creates a note dated today, mid-day. The due
    # moment (local midnight) is in the past, but creation on the same local
    # day must not count as born-past-due.
    s = session_factory()
    note = make_note(s, make_user(s), TODAY, created_at=datetime(2026, 6, 10, 11, 30))

    await scheduler.process_due_notes()

    assert get_row(s, note.id).status == "sent"
    assert len(adapter.sent) == 1
    s.close()
