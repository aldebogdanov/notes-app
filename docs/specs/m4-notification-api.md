# M4 — Notification settings & Telegram linking API

Implementation spec for milestone M4 from `SPEC.md`. Scope: the HTTP surface for what
M1–M3 built — read/update notification settings, link/verify/unlink Telegram. No UI
(M5/M6). After M4 the full reminder path works end-to-end via curl.

## 1. Endpoints (`app/routers/notifications.py`, prefix `/api/account/notifications`)

All require auth (`get_current_user`). New router file, tag `notifications`.

| method/path | body → response | notes |
|---|---|---|
| `GET /settings` | → `NotificationSettingsOut` | derived view, never raw JSON |
| `PUT /settings` | `NotificationSettingsIn` → `NotificationSettingsOut` | partial update; calls `notify_change()` |
| `POST /telegram/link` | → `TelegramLinkOut` | issues one-time code + deep link |
| `POST /telegram/verify` | → `NotificationSettingsOut` | polls `getUpdates` once, binds `chat_id` |
| `DELETE /telegram/link` | → `NotificationSettingsOut` | clears `chat_id`, disables channel |

### Schemas (`app/schemas.py`)

```python
class NotificationChannelOut(BaseModel):
    available: bool        # adapter configured server-side (registry)
    enabled: bool
    linked: bool           # chat_id present — the id itself is NEVER returned

class NotificationSettingsOut(BaseModel):
    timezone: str
    channels: dict[str, NotificationChannelOut]   # keyed by KNOWN_CHANNELS

class TelegramChannelIn(BaseModel):
    enabled: bool

class NotificationSettingsIn(BaseModel):          # all fields optional = partial update
    timezone: str | None = None
    channels: dict[str, TelegramChannelIn] | None = None

class TelegramLinkOut(BaseModel):
    code: str
    deep_link: str         # https://t.me/<bot_username>?start=<code>
    expires_in: int        # seconds
```

### Rules

- `PUT` timezone validated via `zoneinfo` → 422 on unknown name.
- `PUT` `channels` keys outside `KNOWN_CHANNELS` → 422.
- `PUT` `enabled=true` while unlinked → **409** `"Link telegram first"` (UI enforces the
  same order).
- Every successful `PUT` calls `scheduler.notify_change()` (freshly-enabled due notes
  fire on the next pass instead of the next rescan). Scheduler may be absent
  (`app.state.scheduler is None` in tests) — no-op then.
- `chat_id` never leaves the backend: `GET` exposes `linked: bool` only.

## 2. Linking flow

1. `POST /telegram/link`:
   - no telegram adapter in registry → **503** `"Telegram is not configured on this server"`;
   - generate `code = secrets.token_urlsafe(8)`, store
     `channels.telegram.link_code` + `link_code_issued_at` (UTC ISO) in
     `notification_settings`; TTL **10 minutes**;
   - response: code, `https://t.me/{bot_username}?start={code}`, expires_in 600.
2. User presses Start (Telegram sends `/start <code>` from their chat).
3. `POST /telegram/verify`:
   - no adapter → 503; no/expired stored code → **400** `"No active link code"`;
   - call `get_updates()` (single short poll, no offset bookkeeping), scan for messages
     whose text is `/start <code>`; found → store `chat_id`, drop code fields, response
     `linked: true`; not found → **404** `"Press Start in Telegram, then retry"`;
   - rate-limited per user (reuse `auth_rate_limit_key`/`check_auth_rate_limit` from
     `app/rate_limit.py`, purpose `"tg-verify"`) — `getUpdates` must not be hammered.
4. `DELETE /telegram/link`: drop `chat_id` + code fields, force `enabled=false`.

Linking is channel-specific by nature — these flows do **not** widen the
`NotificationAdapter` protocol (stays send-only). The endpoints pull the concrete
`TelegramAdapter` from the registry.

## 3. Adapter additions (`app/notifications/telegram.py`)

- `async get_bot_username() -> str` — `getMe`, cached on the instance after first call;
- `async get_updates() -> list[TelegramUpdate]` where `TelegramUpdate` is a small
  dataclass `(chat_id: int, text: str)` — `getUpdates` with `timeout=0`, flattening
  `message.chat.id` / `message.text`, ignoring everything malformed;
- both raise `NotificationSendError` on transport failure (same retry policy class as
  `send`: transient-retry, hard-4xx fail fast — reuse the existing `_post`/retry helper,
  refactored to a private `_call(method, payload)`).

## 4. Registry exposure

Lifespan (M3) builds the registry; M4 also stores it on `app.state.adapter_registry`
(built even when the scheduler is disabled — it's cheap and endpoints need it).
Endpoints read it via `request.app.state`. Tests inject fakes by setting
`app.state.adapter_registry` in a fixture (mirrors the scheduler tests' approach).

## 5. Storage shape (extends M1's documented shape, same column)

```json
{
  "timezone": "Europe/Berlin",
  "channels": {
    "telegram": {
      "enabled": true,
      "chat_id": 123456789,
      "link_code": "abc12345",          // transient, dropped after verify/unlink
      "link_code_issued_at": "2026-06-10T12:00:00+00:00"
    }
  }
}
```

`get_channel_config` (M3) ignores the transient fields — no scheduler changes.

## 6. Tests (`backend/tests/test_notification_api.py`)

Fake adapter exposing scripted `get_bot_username`/`get_updates`; injected via
`app.state.adapter_registry`. Existing `client` fixture; auth helper as in other tests.

1. GET defaults: empty settings → UTC, telegram `{available: false, enabled: false, linked: false}`;
   with fake registry → `available: true`;
2. PUT timezone: valid stored; invalid → 422; unknown channel key → 422;
3. PUT enable unlinked → 409; enable after link → 200 and `notify_change` called
   (spy scheduler on `app.state`);
4. link: tokenless → 503; with fake → code+deep_link+expires, code persisted;
5. verify happy path: fake `get_updates` returns `/start <code>` → linked, code dropped,
   `chat_id` stored (assert via DB, not via API);
6. verify misses: no code issued → 400; expired code (issued_at 11 min ago) → 400;
   updates without the code → 404; rate limit kicks in after N rapid calls → 429;
7. unlink: linked+enabled user → unlinked, disabled;
8. GET never leaks chat_id (response JSON string scan);
9. openapi snapshot updated (`make openapi-dump`) — existing snapshot test guards it;
10. adapter unit tests for `get_bot_username` (cache: second call = no HTTP) and
    `get_updates` (parse, malformed-update tolerance) with respx.

## 7. DoD mapping

| DoD item | M4 |
|---|---|
| `make up && make seed` | unaffected |
| `make test` ≥ 80% | §6 |
| `make lint` | ruff + format |
| `openapi.json` | **regenerated** — five new endpoints |
| migration downgrade | no migration (same JSON column) — N/A |
| i18n EN+RU | backend only — N/A (UI strings arrive in M5) |
| `.env.example` | no new env vars — N/A |

## 8. Risks / notes

- `getUpdates` returns nothing if the bot has a webhook configured — documented in
  README (M7): the bot used for this app must not have a webhook.
- `getUpdates` without offset re-reads recent updates; harmless here (we only match the
  exact active code, single-use, 10-min TTL) and avoids offset state entirely.
- Sync DB calls inside async endpoints (link/verify are `async def` for the adapter
  calls): single-row reads/writes, microseconds — acceptable; noted for SUBMISSION.
- Two users linking the same Telegram account: allowed (each binds own chat_id) — not a
  conflict.
- SPEC.md gets progress marks (M1–M3 ✅) in this PR — the rule existed but wasn't applied.
