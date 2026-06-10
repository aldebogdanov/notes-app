# SPEC — Telegram reminders for note dates

Test issue for Enji.ai (see `issue.pdf`). Goal: when a note's `note_date` arrives, the user
receives a Telegram reminder — if they linked Telegram and enabled notifications.

## Design decisions

### Data model
- **`users.notification_settings`** — JSON, NOT NULL, default `{}`. Shape:
  ```json
  {
    "timezone": "Europe/Berlin",            // IANA tz; UI pre-fills with browser/system tz
                                            // (Intl.DateTimeFormat().resolvedOptions().timeZone),
                                            // server-side fallback "UTC" if never set
    "channels": {
      "telegram": {"enabled": false, "chat_id": null}
      // future: "discord": {...}, "slack": {...}
    }
  }
  ```
  Missing keys mean defaults (disabled, UTC). Per-channel `enabled` is the on/off trigger.
- **`note_notifications`** — new table, one row per (note, channel):
  - `id` PK, `note_id` FK → notes ON DELETE CASCADE, `channel` VARCHAR(32)
    (equals adapter registry name, e.g. `"telegram"`), `UNIQUE(note_id, channel)`;
  - `status` — `pending | sent | skipped | failed` (`failed` = retries exhausted);
  - `attempts` INT default 0, `last_error` TEXT nullable, `sent_at` timestamptz nullable,
    `created_at` / `updated_at`.
  - **No row = nothing processed yet** (note not due / not visited). The scheduler creates the
    row when it claims a due note (`pending`), then moves it to a terminal status. Adding a new
    channel later = new adapter name in the registry, rows appear lazily — no migration.
  - Note API responses expose a derived `notification_status` map
    (`{"telegram": "pending" | "sent" | "skipped" | "failed"}`, computed from rows; absent row
    for a registered channel reads as `pending` when the note has an upcoming `note_date`).
- **Backfill in migration / seed**: notes with `note_date` strictly before today → insert
  `(note_id, "telegram", "skipped")` rows; today/future/no date → no rows. All channels
  disabled by default.
- Migration must have a working `downgrade()`; `make seed` must work after it.

### Due semantics (timezones)
`note_date` is a DATE with no time component. A note becomes **due at 00:00 of `note_date`
in the user's timezone** ("the moment the date arrives"). Therefore:
- new/existing note with `note_date` **< today** (user tz) → `skipped` immediately;
- `note_date` **== today** → due now → processed on next scheduler pass (good for demos);
- `note_date` **> today** → pending (`NULL`).

### Notification adapters (channel-agnostic)
- `NotificationAdapter` protocol: `name`, `is_configured` (server side: env var present),
  `send(chat_ref, text) -> None` (raises on failure).
- `TelegramAdapter` — the only implementation now; uses Bot API via `httpx`,
  token from optional env var `TELEGRAM_BOT_TOKEN`. No token → adapter not registered,
  app boots fine, everything degrades to `skipped`.
- Registry of enabled adapters is built at startup and injected into the scheduler
  (easy to extend with Discord/Slack later; easy to inject a fake in tests).

### Scheduler
- Single **asyncio background task** started/stopped via FastAPI lifespan (not
  `threading.Timer` — see rationale in PR description; same semantics, fewer footguns).
- Loop: compute next wake-up = earliest upcoming due moment across users' timezones;
  sleep until then (capped by a safety re-scan interval, e.g. 15 min); on wake, process due notes.
- **Recalculation**: note create/update/delete signals the scheduler (asyncio.Event) to
  recompute its sleep — covers "timer recalculates on new note".
- **Processing pass** (idempotent): select due notes having no `note_notifications` row for a
  registered channel (or a `pending` row with `attempts < max`); claim by upserting the row as
  `pending`; user linked + enabled + adapter configured → `send()` then `sent` (+`sent_at`);
  otherwise `skipped`. Send failure → `attempts += 1`, `last_error` recorded, retry with
  backoff; after max attempts → `failed`.
- **Idempotency**: the `UNIQUE(note_id, channel)` row is claimed (committed as `pending`)
  before sending and finalized after; single scheduler instance (single backend process) means
  a message is never sent twice, including across restarts. Multi-worker would add
  `SELECT … FOR UPDATE SKIP LOCKED` on these rows — out of scope.
- **Clock injection**: scheduler takes a `now()` callable and the adapter registry as
  dependencies, so tests drive time without sleeping (plus `time-machine` where simpler).

### Telegram linking flow (UX)
1. Settings shows a one-time **link code** and a `t.me/<bot>?start=<code>` deep link.
2. User opens the bot, presses Start (sends `/start <code>`).
3. User clicks "Verify" in Settings → backend calls `getUpdates` on demand, finds the code,
   stores `chat_id`. No permanent polling/webhook needed.
4. Unlink button clears `chat_id` and disables the channel.

### API (all under `/api/account/notifications`)
- `GET    /settings` — current settings (chat_id masked).
- `PUT    /settings` — update timezone / per-channel enabled flags.
- `POST   /telegram/link` — issue link code + deep link.
- `POST   /telegram/verify` — confirm linking (polls getUpdates once).
- `DELETE /telegram/link` — unlink.
`backend/openapi.json` regenerated (`make openapi-dump`) whenever endpoints change.

## Milestones

One spec PR + one implementation PR per milestone, in order (see CLAUDE.md workflow).
Status: ✅ = implementation merged.

- ✅ **M1 — Schema & seed** (`feat/m1-notification-schema`)
  Migration 0003: `users.notification_settings` + `note_notifications` table (+ downgrade),
  models/Pydantic schema updates (derived `notification_status` map in note responses),
  backfill rule, seed extended with past/today/future dated notes and skipped rows.
  Tests for defaults and backfill.

- ✅ **M2 — Notification adapter layer** (`feat/m2-notification-adapters`)
  `NotificationAdapter` protocol, `TelegramAdapter` (httpx, retries with backoff), adapter
  registry, `TELEGRAM_BOT_TOKEN` in config + `.env.example` + docker-compose passthrough.
  Unit tests with mocked HTTP (respx); no scheduler yet.

- ✅ **M3 — Scheduler** (`feat/m3-reminder-scheduler`)
  Lifespan-managed asyncio scheduler: due-note query, processing pass (sent/skipped/failed),
  wake-up recalculation hooked into note create/update/delete, injectable clock.
  Tests cover all branches with fake clock + fake adapter (no real sleeps, no real Telegram).

- ✅ **M4 — Settings & linking API** (`feat/m4-notification-api`)
  Endpoints above, link-code flow (`getUpdates` on demand), validation (IANA tz), rate-limit
  reuse where sensible, `openapi.json` regenerated, API tests.

- ✅ **M5 — Frontend: notification settings** (`feat/m5-settings-ui`)
  Notifications panel in Settings: link/verify/unlink Telegram, per-channel enable toggle,
  timezone select. `api.js` methods. i18n strings EN + RU. Component tests.

- ❌ **M6 — Frontend: toasts** (dropped 2026-06-10)
  Scope cut: not required by the task. The underlying observation — the scaffold has no
  feedback layer at all (silent successes, per-page inline error divs duplicated 5×, raw
  `window.confirm`) — moves to SUBMISSION.md "scaffold observations / next steps" (M7).

- **M7 — Docs, demo & polish** (`feat/m7-docs-demo`)
  README section (setup, bot token, linking), demo script — exact command sequence proving
  the bot sends a real message, `SUBMISSION.md` (tools used & why, scaffold observations,
  next steps, known caveats). Final DoD sweep.

- **M8 (optional) — Public sharing** (`feat/m8-public-sharing`)
  Read-only share link per note: share token column, `POST/DELETE /api/notes/{id}/share`,
  public `GET /api/public/notes/{token}`, UI button + copy link, i18n, tests.

- **M9 (optional) — Export** (`feat/m9-export`)
  `GET /api/notes/{id}/export` (markdown) and `GET /api/notes/export` (zip of all notes),
  UI buttons, tests.

## Definition of Done (every PR)

1. `make up && make seed` works on a clean machine.
2. `make test` green; backend coverage ≥ 80% (`pytest.ini` gate).
3. `make lint` green (ruff + eslint).
4. Endpoint changed → `backend/openapi.json` regenerated.
5. New migration has working `downgrade()`; `make seed` still works.
6. Every new UI string exists in both EN and RU (`frontend/src/i18n.jsx`).
7. New env vars reflected in `backend/.env.example` (and docker-compose if needed).

## Out of scope

Real Discord/Slack adapters, webhook-based Telegram updates, multi-worker scheduler
coordination (single backend process assumed), per-note custom reminder time.
