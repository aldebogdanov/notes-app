from datetime import UTC, datetime, timedelta

import pytest

from app.main import app
from app.notifications import NotificationSendError
from app.notifications.telegram import TelegramUpdate
from app.routers import notifications as notifications_router

SETTINGS_URL = "/api/account/notifications/settings"
LINK_URL = "/api/account/notifications/telegram/link"
VERIFY_URL = "/api/account/notifications/telegram/verify"


def _auth(client, username="user1", password="pw123456"):
    client.post("/api/auth/register", json={"username": username, "password": password})
    r = client.post(
        "/api/auth/login",
        data={"username": username, "password": password},
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


class FakeTelegram:
    name = "telegram"

    def __init__(self):
        self.username = "test_reminder_bot"
        self.updates: list[TelegramUpdate] = []
        self.fail = False

    async def get_bot_username(self) -> str:
        if self.fail:
            raise NotificationSendError("down")
        return self.username

    async def get_updates(self) -> list[TelegramUpdate]:
        if self.fail:
            raise NotificationSendError("down")
        return list(self.updates)

    async def send(self, chat_ref: str, text: str) -> None:  # pragma: no cover
        pass


class SpyScheduler:
    def __init__(self):
        self.notified = 0

    def notify_change(self) -> None:
        self.notified += 1


@pytest.fixture
def fake_tg(client):
    adapter = FakeTelegram()
    app.state.adapter_registry = {"telegram": adapter}
    yield adapter
    app.state.adapter_registry = {}


def _link_and_verify(client, headers, fake_tg, chat_id=777111):
    code = client.post(LINK_URL, headers=headers).json()["code"]
    fake_tg.updates = [TelegramUpdate(chat_id=chat_id, text=f"/start {code}")]
    r = client.post(VERIFY_URL, headers=headers)
    assert r.status_code == 200
    return r


# --- GET /settings ---


def test_get_settings_defaults_tokenless(client):
    h = _auth(client)
    body = client.get(SETTINGS_URL, headers=h).json()
    assert body == {
        "timezone": "UTC",
        "channels": {"telegram": {"available": False, "enabled": False, "linked": False}},
    }


def test_get_settings_available_with_registry(client, fake_tg):
    h = _auth(client)
    body = client.get(SETTINGS_URL, headers=h).json()
    assert body["channels"]["telegram"]["available"] is True


# --- PUT /settings ---


def test_put_timezone_valid_and_invalid(client):
    h = _auth(client)
    r = client.put(SETTINGS_URL, headers=h, json={"timezone": "Europe/Berlin"})
    assert r.status_code == 200
    assert r.json()["timezone"] == "Europe/Berlin"
    assert client.get(SETTINGS_URL, headers=h).json()["timezone"] == "Europe/Berlin"

    assert client.put(SETTINGS_URL, headers=h, json={"timezone": "Mars/Olympus"}).status_code == 422


def test_put_unknown_channel_rejected(client):
    h = _auth(client)
    r = client.put(SETTINGS_URL, headers=h, json={"channels": {"discord": {"enabled": True}}})
    assert r.status_code == 422


def test_put_enable_unlinked_conflicts(client, fake_tg):
    h = _auth(client)
    r = client.put(SETTINGS_URL, headers=h, json={"channels": {"telegram": {"enabled": True}}})
    assert r.status_code == 409


def test_put_enable_after_link_notifies_scheduler(client, fake_tg):
    h = _auth(client)
    _link_and_verify(client, h, fake_tg)

    spy = SpyScheduler()
    app.state.scheduler = spy
    try:
        r = client.put(SETTINGS_URL, headers=h, json={"channels": {"telegram": {"enabled": True}}})
    finally:
        app.state.scheduler = None
    assert r.status_code == 200
    assert r.json()["channels"]["telegram"] == {
        "available": True,
        "enabled": True,
        "linked": True,
    }
    assert spy.notified == 1


# --- link / verify / unlink ---


def test_link_tokenless_503(client):
    h = _auth(client)
    assert client.post(LINK_URL, headers=h).status_code == 503
    assert client.post(VERIFY_URL, headers=h).status_code == 503


def test_link_returns_code_and_deep_link(client, fake_tg):
    h = _auth(client)
    body = client.post(LINK_URL, headers=h).json()
    assert body["expires_in"] == 600
    assert body["deep_link"] == f"https://t.me/test_reminder_bot?start={body['code']}"
    assert len(body["code"]) >= 8


def test_link_telegram_api_down_502(client, fake_tg):
    h = _auth(client)
    fake_tg.fail = True
    assert client.post(LINK_URL, headers=h).status_code == 502


def test_verify_happy_path(client, fake_tg):
    h = _auth(client)
    r = _link_and_verify(client, h, fake_tg)
    assert r.json()["channels"]["telegram"]["linked"] is True

    # code is single-use: dropped after verify
    r = client.post(VERIFY_URL, headers=h)
    assert r.status_code == 400


def test_verify_without_code_400(client, fake_tg):
    h = _auth(client)
    assert client.post(VERIFY_URL, headers=h).status_code == 400


def test_verify_expired_code_400(client, fake_tg, monkeypatch):
    h = _auth(client)
    code = client.post(LINK_URL, headers=h).json()["code"]
    fake_tg.updates = [TelegramUpdate(chat_id=1, text=f"/start {code}")]

    monkeypatch.setattr(
        notifications_router,
        "_utcnow",
        lambda: datetime.now(UTC) + timedelta(seconds=601),
    )
    assert client.post(VERIFY_URL, headers=h).status_code == 400


def test_verify_no_matching_update_404_then_rate_limited(client, fake_tg):
    h = _auth(client)
    client.post(LINK_URL, headers=h)
    fake_tg.updates = [TelegramUpdate(chat_id=1, text="/start wrong-code")]

    statuses = [client.post(VERIFY_URL, headers=h).status_code for _ in range(10)]
    assert statuses[0] == 404
    assert 429 in statuses  # rate limiter kicked in


def test_unlink_clears_and_disables(client, fake_tg):
    h = _auth(client)
    _link_and_verify(client, h, fake_tg)
    client.put(SETTINGS_URL, headers=h, json={"channels": {"telegram": {"enabled": True}}})

    r = client.request("DELETE", "/api/account/notifications/telegram/link", headers=h)
    assert r.status_code == 200
    assert r.json()["channels"]["telegram"] == {
        "available": True,
        "enabled": False,
        "linked": False,
    }
    # enabling again requires a fresh link
    r = client.put(SETTINGS_URL, headers=h, json={"channels": {"telegram": {"enabled": True}}})
    assert r.status_code == 409


def test_chat_id_never_leaks(client, fake_tg):
    h = _auth(client)
    _link_and_verify(client, h, fake_tg, chat_id=777111)

    for response in (
        client.get(SETTINGS_URL, headers=h),
        client.put(SETTINGS_URL, headers=h, json={"timezone": "UTC"}),
    ):
        assert "777111" not in response.text
