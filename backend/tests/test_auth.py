import pytest
from pydantic import ValidationError

from app.auth import create_access_token
from app.config import Settings
from app.deps import get_current_user
from app.models import User


def test_register_and_login(client):
    r = client.post("/api/auth/register", json={"username": "alice", "password": "secret123"})
    assert r.status_code == 201
    assert r.json()["username"] == "alice"

    r = client.post(
        "/api/auth/login",
        data={"username": "alice", "password": "secret123"},
    )
    assert r.status_code == 200
    assert "access_token" in r.json()


def test_register_duplicate(client):
    client.post("/api/auth/register", json={"username": "bob", "password": "secret123"})
    r = client.post("/api/auth/register", json={"username": "bob", "password": "otherpass"})
    assert r.status_code == 409


def test_login_bad_password(client):
    client.post("/api/auth/register", json={"username": "carol", "password": "secret123"})
    r = client.post(
        "/api/auth/login",
        data={"username": "carol", "password": "wrong"},
    )
    assert r.status_code == 401


def test_login_bad_password_is_rate_limited(client):
    client.post("/api/auth/register", json={"username": "dora", "password": "secret123"})

    for _ in range(5):
        r = client.post(
            "/api/auth/login",
            data={"username": "dora", "password": "wrong"},
        )
        assert r.status_code == 401

    r = client.post(
        "/api/auth/login",
        data={"username": "dora", "password": "wrong"},
    )
    assert r.status_code == 429


def test_protected_route_requires_token(client):
    r = client.get("/api/notes")
    assert r.status_code == 401


def test_jwt_secret_is_required(monkeypatch):
    monkeypatch.delenv("JWT_SECRET", raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize(
    "secret",
    [
        "change-me-in-production",
        "replace-with-at-least-32-random-characters",
        "test-secret",
        "short-secret",
    ],
)
def test_jwt_secret_rejects_weak_values(monkeypatch, secret):
    monkeypatch.setenv("JWT_SECRET", secret)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_strong_jwt_secret_still_signs_and_verifies_tokens(client, monkeypatch):
    secret = "strong-test-secret-with-at-least-32-characters"
    monkeypatch.setattr("app.auth.settings.jwt_secret", secret)
    monkeypatch.setattr("app.deps.settings.jwt_secret", secret)

    token = create_access_token(42)
    user = get_current_user(
        token=token,
        db=type(
            "SessionStub",
            (),
            {"get": lambda self, model, user_id: User(id=user_id, username="alice")},
        )(),
    )

    assert user.id == 42
