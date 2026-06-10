# M2 — Notification adapter layer

Implementation spec for milestone M2 from `SPEC.md`. Scope: channel-agnostic adapter
abstraction + the Telegram implementation + runtime registry. No scheduler (M3), no
endpoints (M4), no UI. Nothing calls the adapters in production code paths yet — M2 ships
the layer fully tested, M3 wires it.

## 1. Module layout (`backend/app/notifications/`)

```
__init__.py    — KNOWN_CHANNELS, NotificationStatus, notification_status_map (from M1),
                 plus re-exports: get_adapter_registry, NotificationSendError
base.py        — NotificationAdapter protocol + NotificationSendError
telegram.py    — TelegramAdapter
registry.py    — build_adapter_registry(settings) -> dict[str, NotificationAdapter]
```

## 2. Adapter contract (`base.py`)

```python
class NotificationSendError(Exception):
    """Send failed after adapter-level retries; message carries the reason."""


class NotificationAdapter(Protocol):
    name: str                                   # registry key, equals channel column value

    async def send(self, chat_ref: str, text: str) -> None:
        """Deliver text to chat_ref. Raises NotificationSendError on failure."""
```

Async because the M3 scheduler is an asyncio task. `chat_ref` is the channel-specific
recipient id (`chat_id` for Telegram) stored in `users.notification_settings`.

## 3. TelegramAdapter (`telegram.py`)

- `TelegramAdapter(token, *, base_url="https://api.telegram.org", client=None, sleep=anyio.sleep)`
  — injectable `httpx.AsyncClient` and `sleep` for tests (no real network, no real sleeps).
- `send()` POSTs `{base_url}/bot{token}/sendMessage` with `{"chat_id": chat_ref, "text": text}`.
- In-call retry policy for transient failures only, max 3 attempts:

  | response | action |
  |---|---|
  | 200 | done |
  | 429 | wait `retry_after` from body (fallback: backoff), retry |
  | 5xx / network error (`httpx.HTTPError`) | backoff 0.5s → 1s → 2s, retry |
  | other 4xx (bad chat_id, bot blocked, bad token) | `NotificationSendError` immediately, no retry |

- After attempts exhausted → `NotificationSendError`.
- **Length limit is the adapter's concern**: Telegram caps `text` at 4096 chars, and only
  the adapter knows its channel's cap. `send()` truncates longer text to 4095 chars + `…`
  before POSTing. Callers (M3 composer) pass whatever they build; future adapters apply
  their own channel caps.
- Retry split vs M3: adapter retries *transient* errors within one send call; the
  scheduler's `attempts`/`failed` columns (M1) count *send calls* across passes. Two
  layers, two concerns: in-call jitter vs durable outcome.
- Token never logged; error messages include HTTP status + Telegram `description` only.

## 4. Registry (`registry.py`)

```python
def build_adapter_registry(settings) -> dict[str, NotificationAdapter]
```

- Returns `{"telegram": TelegramAdapter(...)}` when `settings.telegram_bot_token` is set,
  `{}` otherwise. App boots and tests pass without the token (DoD-critical).
- Built once at startup (M3 lifespan keeps it; M4 endpoints read it via dependency).
  M2 only provides the function + a `get_adapter_registry(settings)` convenience with
  `functools.lru_cache` semantics avoided deliberately (tests build throwaway registries).
- **Clarification vs M1 note** (“registry replaces KNOWN_CHANNELS”): both stay, different
  meanings. `KNOWN_CHANNELS` = channels the *product* implements (drives `pending`
  derivation in `notification_status_map` — a user with an unconfigured server still sees
  `pending`, and the M3 scheduler resolves it to `skipped` when due). Registry = adapters
  *configured in this deployment*. `KNOWN_CHANNELS` stays the static tuple `("telegram",)`;
  M1's spec note is amended by this section.

## 5. Config / env

- `app/config.py`: `telegram_bot_token: str | None = None` (pydantic-settings reads
  `TELEGRAM_BOT_TOKEN`). No validator — any non-empty string accepted, Telegram rejects
  bad tokens at call time.
- `backend/.env.example`: `TELEGRAM_BOT_TOKEN=` (empty = disabled) + comment.
- `docker-compose.yml` backend env: `TELEGRAM_BOT_TOKEN: ${TELEGRAM_BOT_TOKEN:-}` —
  passes the host var through, empty default keeps `make up` green on clean machines.
- `requirements.txt`: add `respx>=0.21` (test-only; scaffold keeps a single requirements
  file, follow it).

## 6. Tests (`backend/tests/test_notification_adapters.py`)

Async tests via the anyio pytest plugin (ships with fastapi's anyio dependency;
`anyio_backend` fixture pinned to `"asyncio"`). Telegram HTTP mocked with respx;
`sleep` replaced by a recorder — assert backoff sequence, zero real waiting.

1. success: one POST, correct URL/payload, no retries;
2. 429 with `retry_after: 7` → sleep(7) recorded, second attempt succeeds;
3. 500, 500, 200 → backoff 0.5/1 recorded, succeeds on third;
4. three 500s → `NotificationSendError`, exactly 3 attempts;
5. 403 (bot blocked) → immediate `NotificationSendError`, single attempt, no sleep;
6. network error (`httpx.ConnectError`) → retried like 5xx;
7. error message contains status + description, never the token;
8. registry: token set → `{"telegram": ...}`; token None/empty → `{}`;
9. app boot smoke without token (existing `client` fixture already runs tokenless —
   assert registry empty via direct call);
10. text longer than 4096 chars → POSTed `text` is exactly 4096 (4095 + `…`); shorter
    text passes through unchanged.

Coverage stays ≥ 80% (adapter+registry are small, fully covered).

## 7. DoD mapping

| DoD item | M2 |
|---|---|
| `make up && make seed` | unaffected; compose passthrough keeps empty default |
| `make test` ≥ 80% | §6 |
| `make lint` | ruff + format on new files |
| `openapi.json` | no endpoint changes — N/A |
| migration downgrade | no migration — N/A |
| i18n EN+RU | no UI strings — N/A |
| `.env.example` | `TELEGRAM_BOT_TOKEN` added (+ compose passthrough) |

## 8. Risks / notes

- anyio pytest plugin auto-parametrizes asyncio+trio if `anyio_backend` fixture is not
  pinned — pin it to `"asyncio"` in the test module (trio not installed).
- respx must match the tokenized URL; use `url__regex` or mount on base_url to avoid
  embedding the fake token twice.
- Telegram counts the 4096 limit in UTF-16 code units when entities are involved; for the
  plain-text messages we send, a Python `len()` cut is correct enough — noted for honesty.
