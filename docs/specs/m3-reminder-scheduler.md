# M3 — Reminder scheduler

Implementation spec for milestone M3 from `SPEC.md`. Scope: the background job that turns
`pending` into `sent`/`skipped`/`failed` and actually calls the M2 adapters. No new
endpoints (M4), no UI (M5/M6). After M3 a linked+enabled user receives a Telegram message
when a note's date arrives.

## 1. Due semantics (recap from SPEC.md, now binding)

- A note is **due** at 00:00 of `note_date` in its owner's IANA timezone
  (`notification_settings.timezone`, invalid/missing → UTC). Equivalent test:
  `note_date <= today_in_owner_tz`.
- Due + no terminal row → the scheduler must resolve it this pass.
- **Born past-due vs missed**: a note created on a *later local day* than its
  `note_date` was never a live reminder → `skipped` (the SPEC.md "new note with passed
  date" rule). A note created on its own date — or earlier but processed late (server
  downtime) → normal send; same-day means the date has just arrived, catch-up beats
  silent loss. One day-level comparison distinguishes the cases — no creation hook.
  *(Amended in M7: the original instant-level comparison `created_at > due moment`
  wrongly skipped the today-note-created-today case — the live-demo path.)*
- **Archived notes are skipped** (product call: archived = put away, no reminders).
  Unarchiving after the date has passed does not resurrect the reminder.
- A `skipped`/`sent`/`failed` row is terminal forever — enabling notifications later never
  resends old reminders.

## 2. Scheduler (`app/notifications/scheduler.py`)

```python
class ReminderScheduler:
    def __init__(
        self,
        session_factory,                  # () -> Session; app passes SessionLocal
        registry: dict[str, NotificationAdapter],
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),   # injectable clock
        rescan_interval: float = 900.0,   # safety net, seconds
        max_attempts: int = 3,            # send calls across passes per (note, channel)
    )
    async def run(self) -> None          # the loop; cancellation-safe
    async def process_due_notes(self) -> None   # one pass, also unit-test entry point
    def notify_change(self) -> None      # thread-safe wake-up from sync endpoints
```

### Static registry vs live user settings

The constructor-injected registry is **deployment** config: which adapters hold server
credentials (`TELEGRAM_BOT_TOKEN` and future tokens come from env vars, which only change
with a process restart — lifespan rebuilds the registry then). Nothing in the UI can ever
add or remove a registry entry, so the scheduler is never recreated at runtime.

Everything the UI *does* change (M4/M5 — per-user `enabled` toggles, `chat_id` linking,
timezone) lives in `users.notification_settings` and is read **fresh from the DB on every
pass** via `get_channel_config(user, channel)` at processing time — never cached on the
scheduler. A toggle flipped in Settings is honored by the very next pass with no
restart and no signal (though M4 may also call `notify_change()` on settings writes so a
freshly-enabled due note fires immediately instead of waiting for the next wake-up).

### Loop

1. `process_due_notes()`.
2. Compute next wake instant: min local-midnight > now over distinct
   `(note_date, owner_tz)` pairs of notes with a non-terminal state; cap the wait at
   `rescan_interval` (safety rescan catches drift, tz changes, missed signals).
3. `await` an `asyncio.Event` with that timeout (`anyio.fail_after`-style); event set or
   timeout → clear event, goto 1.
4. Any exception inside a pass is caught and logged — the loop must never die silently
   (the original `threading.Timer` failure mode this design replaces).

### One pass

For each due note (join owner, exclude archived) × each `KNOWN_CHANNELS` entry without a
terminal row:

| condition | outcome |
|---|---|
| note created after its due moment (born past-due) | `skipped` |
| channel enabled + chat linked + adapter in registry | claim → send → `sent` (+`sent_at`) |
| send raises `NotificationSendError` | `attempts += 1`, `last_error`; `attempts >= max_attempts` → `failed`, else row stays `pending` for retry next pass |
| disabled, unlinked, or adapter not configured | `skipped` immediately |
| note archived | `skipped` |

**Claim protocol**: upsert the row as `pending` with `attempts += 1` and **commit before
sending**; finalize (`sent`/`failed`) and commit after. Crash between send and finalize →
at-least-once delivery (possible duplicate after restart) — accepted and documented;
the alternative (commit `sent` first) silently loses messages. Single scheduler instance
(one backend process) — multi-worker would need `FOR UPDATE SKIP LOCKED`, out of scope.

### Wake-up on note changes

- `notify_change()` uses `loop.call_soon_threadsafe(event.set)` — note CRUD endpoints are
  sync `def`s running in FastAPI's threadpool, a bare `event.set()` is not thread-safe.
- Called from create/update/delete/bulk-delete/archive/unarchive in
  `app/routers/notes.py` via `request.app.state.scheduler` (no-op when scheduler is off,
  e.g. in tests). Pin/unpin don't touch dates — not wired.

### Message composer

`compose_reminder(note) -> str`: `"🔔 {title}\n\n{content}"` (content omitted when
empty). Length capping is the adapter's job (M2). Bot messages are not part of the UI
i18n scope — English only, noted in SUBMISSION.

## 3. App wiring (`app/main.py`)

FastAPI `lifespan` context replaces the bare `app = FastAPI(...)` construction:

- on startup, when `settings.scheduler_enabled`: build registry, create
  `ReminderScheduler(SessionLocal, registry)`, store on `app.state.scheduler`,
  `asyncio.create_task(run())`;
- on shutdown: cancel the task, await it (swallowing `CancelledError`).

New setting `scheduler_enabled: bool = True` (`SCHEDULER_ENABLED`). `tests/conftest.py`
adds `os.environ.setdefault("SCHEDULER_ENABLED", "false")` next to the existing env lines
— the TestClient context manager runs lifespan, and the loop must not run against the
test SQLite engine. Scheduler tests instantiate the class directly instead.

## 4. Settings access helper

`get_channel_config(user, channel) -> ChannelConfig` (dataclass: `enabled: bool`,
`chat_ref: str | None`) in `app/notifications/__init__.py` — single reader for the
`notification_settings` JSON shape; M4 endpoints reuse it. Missing keys → disabled,
unlinked. Owner timezone reader `user_timezone(user) -> ZoneInfo` likewise (invalid tz
string falls back to UTC, never raises).

## 5. Env / compose

- `.env.example`: `SCHEDULER_ENABLED=true` + comment.
- `docker-compose.yml`: not added — default `true` is what the container needs; only
  conftest overrides.

## 6. Tests (`backend/tests/test_reminder_scheduler.py`)

Fixtures: sqlite `session_factory` (as in M1 tests), `FakeClock` (settable aware
datetime), `FakeAdapter` (records `(chat_ref, text)`, optional scripted failures),
registry `{"telegram": fake}`. All async via anyio (asyncio pinned); no real sleeps —
loop tests drive the event and `wait_for` with bounded timeouts.

Pass-level (call `process_due_notes()` directly):
1. enabled + linked + due today → adapter called once, row `sent`, `sent_at` set,
   message contains title (composer smoke);
2. catch-up vs born-past-due: note created before its due moment, processed late →
   `sent`; note created after its due moment (yesterday-dated note created now) →
   `skipped`, adapter not called;
3. disabled / unlinked / adapter missing from registry → `skipped`, adapter not called;
4. archived due note → `skipped`;
5. future note → untouched (no row);
6. terminal rows (`sent`/`skipped`/`failed`) → never reprocessed, adapter not called;
7. send failure → row `pending`, `attempts=1`, `last_error` set; two more failing passes
   → `failed`; recovery: failure then success → `sent` with `attempts=2`;
8. timezone: clock at 2026-06-10 23:00 UTC — Tokyo owner (UTC+9, local 2026-06-11) with
   note dated 2026-06-11 is due; UTC owner with same date is not;
9. invalid timezone string → treated as UTC, no exception;
10. idempotency: second `process_due_notes()` call sends nothing new.

Loop-level (run `run()` as a task):
11. `notify_change()` wakes the loop (new due note processed without waiting for
    rescan timeout);
12. exception inside a pass (adapter raising RuntimeError) is logged, loop survives and
    processes the next wake-up;
13. cancellation: task cancelled cleanly on shutdown (lifespan smoke via TestClient with
    `SCHEDULER_ENABLED=true` and empty registry).

Next-wake computation:
14. earliest upcoming local midnight across mixed timezones chosen; capped by
    `rescan_interval`; no candidates → plain rescan wait.

## 7. DoD mapping

| DoD item | M3 |
|---|---|
| `make up && make seed` | scheduler starts on boot, tokenless registry → due seeds resolve to `skipped`, boot stays green |
| `make test` ≥ 80% | §6 |
| `make lint` | ruff + format |
| `openapi.json` | no endpoint shape changes — N/A (CRUD handlers gain an internal wake-up call only) |
| migration downgrade | no migration — N/A |
| i18n EN+RU | no UI strings — N/A (bot text English, see §2) |
| `.env.example` | `SCHEDULER_ENABLED` added |

## 8. Risks / notes

- TestClient lifespan: every existing test boots the app — `SCHEDULER_ENABLED=false` in
  conftest is the critical line; forgetting it = flaky suite against `:memory:` SQLite.
- `seed` wipes the demo user while the scheduler may be mid-pass on dev stacks — pass
  errors are logged-and-survived, acceptable for dev.
- The born-past-due comparison is day-level in the owner's timezone: a note created
  any time on its own date sends immediately (due moment already passed). Users who
  want "no reminder for a note I just wrote about today" don't get that knob — accepted,
  matches the SPEC.md strictly-past rule. Covered by tests.
- `datetime.now(UTC)` and `ZoneInfo` only — no `pytz`, no naive datetimes anywhere.
