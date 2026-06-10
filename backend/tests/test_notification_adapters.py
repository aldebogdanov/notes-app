import json

import httpx
import pytest
import respx

from app.config import Settings
from app.notifications import NotificationSendError, build_adapter_registry
from app.notifications.telegram import TELEGRAM_MESSAGE_LIMIT, TelegramAdapter

TOKEN = "12345:fake-token"
SEND_URL = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
OK_BODY = {"ok": True, "result": {"message_id": 1}}


@pytest.fixture
def anyio_backend():
    return "asyncio"


class SleepRecorder:
    def __init__(self):
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def make_adapter(sleep: SleepRecorder) -> TelegramAdapter:
    return TelegramAdapter(TOKEN, sleep=sleep)


@pytest.mark.anyio
@respx.mock
async def test_send_success_single_post():
    route = respx.post(SEND_URL).mock(return_value=httpx.Response(200, json=OK_BODY))
    sleep = SleepRecorder()
    await make_adapter(sleep).send("42", "hello")

    assert route.call_count == 1
    body = json.loads(route.calls.last.request.read())
    assert body == {"chat_id": "42", "text": "hello"}
    assert sleep.calls == []


@pytest.mark.anyio
@respx.mock
async def test_429_honors_retry_after():
    route = respx.post(SEND_URL).mock(
        side_effect=[
            httpx.Response(
                429,
                json={
                    "ok": False,
                    "error_code": 429,
                    "description": "Too Many Requests",
                    "parameters": {"retry_after": 7},
                },
            ),
            httpx.Response(200, json=OK_BODY),
        ]
    )
    sleep = SleepRecorder()
    await make_adapter(sleep).send("42", "hi")

    assert route.call_count == 2
    assert sleep.calls == [7.0]


@pytest.mark.anyio
@respx.mock
async def test_5xx_backoff_then_success():
    route = respx.post(SEND_URL).mock(
        side_effect=[
            httpx.Response(500, json={"ok": False, "description": "boom"}),
            httpx.Response(500, json={"ok": False, "description": "boom"}),
            httpx.Response(200, json=OK_BODY),
        ]
    )
    sleep = SleepRecorder()
    await make_adapter(sleep).send("42", "hi")

    assert route.call_count == 3
    assert sleep.calls == [0.5, 1.0]


@pytest.mark.anyio
@respx.mock
async def test_5xx_exhausts_attempts():
    route = respx.post(SEND_URL).mock(
        return_value=httpx.Response(502, json={"ok": False, "description": "bad gateway"})
    )
    sleep = SleepRecorder()
    with pytest.raises(NotificationSendError, match="after 3 attempts"):
        await make_adapter(sleep).send("42", "hi")

    assert route.call_count == 3
    assert sleep.calls == [0.5, 1.0]


@pytest.mark.anyio
@respx.mock
async def test_hard_4xx_fails_fast():
    route = respx.post(SEND_URL).mock(
        return_value=httpx.Response(
            403, json={"ok": False, "description": "Forbidden: bot was blocked by the user"}
        )
    )
    sleep = SleepRecorder()
    with pytest.raises(NotificationSendError, match="blocked"):
        await make_adapter(sleep).send("42", "hi")

    assert route.call_count == 1
    assert sleep.calls == []


@pytest.mark.anyio
@respx.mock
async def test_network_error_retried():
    route = respx.post(SEND_URL).mock(
        side_effect=[
            httpx.ConnectError("boom"),
            httpx.Response(200, json=OK_BODY),
        ]
    )
    sleep = SleepRecorder()
    await make_adapter(sleep).send("42", "hi")

    assert route.call_count == 2
    assert sleep.calls == [0.5]


@pytest.mark.anyio
@respx.mock
async def test_error_message_has_status_and_description_but_no_token():
    respx.post(SEND_URL).mock(
        return_value=httpx.Response(400, json={"ok": False, "description": "chat not found"})
    )
    with pytest.raises(NotificationSendError) as exc_info:
        await make_adapter(SleepRecorder()).send("42", "hi")

    message = str(exc_info.value)
    assert "400" in message
    assert "chat not found" in message
    assert TOKEN not in message


@pytest.mark.anyio
@respx.mock
async def test_network_error_message_has_no_token():
    respx.post(SEND_URL).mock(side_effect=httpx.ConnectError(f"failed to reach {SEND_URL}"))
    with pytest.raises(NotificationSendError) as exc_info:
        await make_adapter(SleepRecorder()).send("42", "hi")

    message = str(exc_info.value)
    assert "ConnectError" in message
    assert TOKEN not in message


@pytest.mark.anyio
@respx.mock
async def test_long_text_truncated_to_limit():
    route = respx.post(SEND_URL).mock(return_value=httpx.Response(200, json=OK_BODY))
    long_text = "x" * (TELEGRAM_MESSAGE_LIMIT + 500)
    await make_adapter(SleepRecorder()).send("42", long_text)

    body = json.loads(route.calls.last.request.read())
    assert len(body["text"]) == TELEGRAM_MESSAGE_LIMIT
    assert body["text"].endswith("…")


@pytest.mark.anyio
@respx.mock
async def test_short_text_passes_unchanged():
    route = respx.post(SEND_URL).mock(return_value=httpx.Response(200, json=OK_BODY))
    await make_adapter(SleepRecorder()).send("42", "short")

    body = json.loads(route.calls.last.request.read())
    assert body["text"] == "short"


def _settings(**overrides) -> Settings:
    return Settings(jwt_secret="x" * 32, **overrides)


def test_registry_with_token():
    registry = build_adapter_registry(_settings(telegram_bot_token=TOKEN))
    assert set(registry) == {"telegram"}
    assert registry["telegram"].name == "telegram"


def test_registry_without_token():
    assert build_adapter_registry(_settings(telegram_bot_token=None)) == {}
    assert build_adapter_registry(_settings(telegram_bot_token="")) == {}


def test_app_boots_tokenless_with_empty_registry(client):
    # The conftest client fixture runs without TELEGRAM_BOT_TOKEN: the app is
    # importable and serving while the registry stays empty.
    from app.config import settings

    assert client.get("/healthz").json() == {"status": "ok"}
    assert build_adapter_registry(settings) == {}
