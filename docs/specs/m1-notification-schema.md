# M1 — Schema & seed for notifications

Implementation spec for milestone M1 from `SPEC.md`. Scope: database layer only — no
adapters, no scheduler, no new endpoints. After M1 the app stores notification settings
per user and notification outcomes per (note, channel), and exposes a derived
`notification_status` map in note responses.

## 1. Goal

- `users.notification_settings` — per-user channel config (timezone, per-channel
  enabled/linking data).
- `note_notifications` — one row per (note, channel) notification outcome.
- Note API responses include derived `notification_status`.
- Seed demonstrates all states (past/today/future/no date).

## 2. Out of scope (later milestones)

- Adapter interface and Telegram HTTP (M2).
- Scheduler, claiming, retries at runtime (M3) — M1 only creates the table they use.
- Settings/linking endpoints and their schemas (M4) — `notification_settings` has no
  public write path in M1; `openapi.json` changes only for `NoteOut`.
- Frontend (M5/M6).

## 3. Migration `0003_notifications.py`

Sequential style of `backend/alembic/versions/0002_archive_pin.py`. All types must work
on both Postgres (runtime) and SQLite (test fixtures in `tests/conftest.py`) — therefore
plain `sa.JSON` (precedent: `notes.tags`) and `sa.String` + CHECK instead of a Postgres
enum.

### upgrade()

1. `users.notification_settings`:
   ```python
   op.add_column(
       "users",
       sa.Column("notification_settings", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
   )
   ```
2. `note_notifications` table:
   ```python
   op.create_table(
       "note_notifications",
       sa.Column("id", sa.Integer(), primary_key=True),
       sa.Column("note_id", sa.Integer(), sa.ForeignKey("notes.id", ondelete="CASCADE"), nullable=False),
       sa.Column("channel", sa.String(32), nullable=False),
       sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
       sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
       sa.Column("last_error", sa.Text(), nullable=True),
       sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
       sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
       sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
       sa.CheckConstraint("status IN ('pending','sent','skipped','failed')", name="ck_note_notifications_status"),
       sa.UniqueConstraint("note_id", "channel", name="uq_note_notifications_note_channel"),
   )
   op.create_index("ix_note_notifications_note_id", "note_notifications", ["note_id"])
   op.create_index("ix_note_notifications_status", "note_notifications", ["status"])
   ```
3. Backfill — notes already past their date are marked `skipped` for `telegram` so the
   future scheduler never picks them up:
   ```python
   op.execute(
       """
       INSERT INTO note_notifications (note_id, channel, status)
       SELECT id, 'telegram', 'skipped' FROM notes
       WHERE note_date IS NOT NULL AND note_date < CURRENT_DATE
       """
   )
   ```
   Backfill uses server-side `CURRENT_DATE` (UTC in our containers). Per-user timezones
   do not exist before this migration, so UTC is the only defensible boundary here;
   per-user-timezone due logic starts with the scheduler (M3).

### downgrade()

Drop indexes, drop `note_notifications`, drop `users.notification_settings`. Must leave
the 0002 schema exactly; verified by test (see §7).

## 4. Models (`backend/app/models.py`)

- `NotificationStatus(enum.StrEnum)`: `PENDING/SENT/SKIPPED/FAILED` — single source of
  truth for the CHECK values; used later by scheduler (M3).
- `KNOWN_CHANNELS: tuple[str, ...] = ("telegram",)` — temporary home in a new
  `backend/app/notifications/__init__.py` package; M2 replaces it with the adapter
  registry keys (same import path, so M1 callers don't change).
- `User.notification_settings: Mapped[dict] = mapped_column(JSON, default=dict, server_default=sa.text("'{}'"))`.
- New model `NoteNotification` mirroring §3, typed `Mapped[...]` style;
  `note: Mapped[Note] = relationship(back_populates="notifications")`.
- `Note.notifications: Mapped[list[NoteNotification]] = relationship(back_populates="note", cascade="all, delete-orphan")`.

`notification_settings` shape (documented in code; enforced by Pydantic only from M4
when a write path appears):

```json
{"timezone": "UTC", "channels": {"telegram": {"enabled": false, "chat_id": null}}}
```

Missing keys = defaults (UTC, disabled, unlinked). `{}` is a valid value.

## 5. Schemas (`backend/app/schemas.py`)

`NoteOut` gets a derived field:

```python
notification_status: dict[str, str] = {}
```

Computed via helper `notification_status_map(note, today)` in
`app/notifications/__init__.py`, not a DB column. *(Implementation notes: exposed as a
`Note.notification_status` property delegating to the helper — endpoints return ORM
objects under `response_model=NoteOut` (`from_attributes`), so a property feeds all of
them without rebuilding `NoteOut` in ~10 routers. `NotificationStatus` also lives in
`app/notifications/`, not `models.py`: `models` imports the helper, enum next to helper
avoids a circular import.)* Rules:

- row exists for channel → its `status`;
- no row, channel in `KNOWN_CHANNELS`, `note_date` is today or future → `"pending"`;
- no row otherwise (no `note_date`, or past date pre-backfill edge) → key omitted.

`NotesPage`, calendar, bulk endpoints reuse `NoteOut` — they pick the field up
automatically, but listing queries must not go N+1: add `selectinload(Note.notifications)`
to the queries in `app/routers/notes.py`.

## 6. Seed (`backend/scripts/seed.py`)

Stays idempotent (demo user wiped first; cascade removes note_notifications rows). Add:

- demo user gets explicit `notification_settings={}` (defaults-as-missing-keys demo);
- new note "Yesterday retro" with `note_date = today - 1 day` **plus** a
  `NoteNotification(channel="telegram", status="skipped")` row — mirrors what the
  migration backfill does for pre-existing rows (seed runs after migration, so the seed
  must create the row itself);
- existing "Grocery list" (today) and "Project kickoff" (tomorrow) get no rows → read
  as `pending` via the derived map.

## 7. Tests (`backend/tests/test_notifications_schema.py` + edits)

1. `NoteOut.notification_status` derivation: today-dated note → `{"telegram": "pending"}`;
   future → `pending`; no `note_date` → `{}`; note with a `skipped` row → `skipped`.
2. List endpoint returns the field for every item (and a query-count assertion or
   `selectinload` smoke check to keep N+1 out).
3. Cascade: deleting a note deletes its `note_notifications` rows; deleting a user
   cascades through notes.
4. `UNIQUE(note_id, channel)` violation raises (IntegrityError at flush).
5. Defaults: new `User` → `notification_settings == {}`; new `NoteNotification` →
   `status == "pending"`, `attempts == 0`.
6. Migration up/down on Postgres (in-container): `alembic upgrade head` →
   `alembic downgrade 0002` → `upgrade head` smoke via subprocess is overkill for the
   suite; instead CI-equivalent manual check goes in the PR description, and the
   downgrade correctness is reviewed by diffing §3. (SQLite fixtures use
   `Base.metadata.create_all`, so migrations aren't exercised by pytest — scaffold
   limitation, noted in SUBMISSION later.)
7. Seed: run `seed()` against the test session twice — idempotent, yesterday-note has
   the `skipped` row.

Coverage must stay ≥ 80% (`pytest.ini` gate) — new code is mostly declarative, the
helper in §5 carries the branches and gets direct unit tests.

## 8. DoD mapping

| DoD item | How M1 satisfies it |
|---|---|
| `make up && make seed` | migration runs on boot; seed extended, idempotent |
| `make test` green, cov ≥ 80% | §7 |
| `make lint` | ruff + format on new files |
| `openapi.json` regenerated | `NoteOut` changed → `make openapi-dump` |
| migration `downgrade()` works | §3, manual up/down/up check in PR description |
| i18n EN+RU | no UI strings in M1 — N/A |
| `.env.example` | no new env vars in M1 — N/A |

## 9. Risks / notes

- `server_default=sa.text("'{}'")` for JSON behaves differently across PG/SQLite quoting —
  verify on both during implementation; fallback is `nullable=True` + app-level `default=dict`
  with a follow-up tightening, but try NOT NULL first.
- Backfill TZ approximation (§3) is deliberate and documented; revisited never — rows are
  terminal `skipped`.
- `KNOWN_CHANNELS` duplication between M1 and M2 avoided by placing it in
  `app/notifications/` from the start.
