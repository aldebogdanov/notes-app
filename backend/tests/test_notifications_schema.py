from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Note, NoteNotification, User
from app.notifications import NotificationStatus, notification_status_map
from scripts import seed as seed_module

TODAY = date.today()


def _auth(client, username="user1", password="pw123456"):
    client.post("/api/auth/register", json={"username": username, "password": password})
    r = client.post(
        "/api/auth/login",
        data={"username": username, "password": password},
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture()
def session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'm1.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    yield factory
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def session(session_factory):
    s = session_factory()
    yield s
    s.close()


def _user(session, username="dbuser"):
    user = User(username=username, password_hash="x")
    session.add(user)
    session.flush()
    return user


# --- notification_status_map derivation ---


def _note(**kwargs):
    note = Note(title="t", **kwargs)
    return note


def test_status_map_today_and_future_are_pending():
    assert notification_status_map(_note(note_date=TODAY), TODAY) == {"telegram": "pending"}
    assert notification_status_map(_note(note_date=TODAY + timedelta(days=3)), TODAY) == {
        "telegram": "pending"
    }


def test_status_map_no_date_is_empty():
    assert notification_status_map(_note(note_date=None), TODAY) == {}


def test_status_map_past_without_row_is_empty():
    assert notification_status_map(_note(note_date=TODAY - timedelta(days=1)), TODAY) == {}


def test_status_map_row_wins_over_derivation():
    note = _note(note_date=TODAY)
    note.notifications = [NoteNotification(channel="telegram", status=NotificationStatus.SENT)]
    assert notification_status_map(note, TODAY) == {"telegram": "sent"}


def test_status_map_unknown_channel_row_is_kept():
    note = _note(note_date=None)
    note.notifications = [NoteNotification(channel="discord", status=NotificationStatus.SKIPPED)]
    assert notification_status_map(note, TODAY) == {"discord": "skipped"}


# --- model defaults / constraints ---


def test_defaults(session):
    user = _user(session)
    note = Note(user_id=user.id, title="n", note_date=TODAY)
    row = NoteNotification(note=note, channel="telegram")
    session.add(note)
    session.commit()

    assert user.notification_settings == {}
    assert row.status == "pending"
    assert row.attempts == 0
    assert row.sent_at is None


def test_unique_note_channel(session):
    user = _user(session)
    note = Note(user_id=user.id, title="n")
    session.add(note)
    session.flush()
    session.add_all(
        [
            NoteNotification(note_id=note.id, channel="telegram"),
            NoteNotification(note_id=note.id, channel="telegram"),
        ]
    )
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_cascade_note_and_user_delete(session):
    user = _user(session)
    note = Note(user_id=user.id, title="n", note_date=TODAY)
    note.notifications = [NoteNotification(channel="telegram", status=NotificationStatus.SKIPPED)]
    session.add(note)
    session.commit()
    assert session.query(NoteNotification).count() == 1

    session.delete(note)
    session.commit()
    assert session.query(NoteNotification).count() == 0

    note2 = Note(user_id=user.id, title="n2")
    note2.notifications = [NoteNotification(channel="telegram")]
    session.add(note2)
    session.commit()
    session.delete(user)
    session.commit()
    assert session.query(Note).count() == 0
    assert session.query(NoteNotification).count() == 0


# --- API exposure ---


def test_note_responses_carry_notification_status(client):
    h = _auth(client)
    r = client.post(
        "/api/notes",
        headers=h,
        json={"title": "due today", "content": "", "note_date": TODAY.isoformat()},
    )
    assert r.status_code == 201
    assert r.json()["notification_status"] == {"telegram": "pending"}

    r = client.post("/api/notes", headers=h, json={"title": "undated", "content": ""})
    assert r.json()["notification_status"] == {}

    body = client.get("/api/notes", headers=h).json()
    assert body["total"] == 2
    assert all("notification_status" in item for item in body["items"])

    nid = body["items"][0]["id"]
    assert "notification_status" in client.get(f"/api/notes/{nid}", headers=h).json()


# --- seed ---


def test_seed_idempotent_and_backfilled(session_factory, monkeypatch):
    monkeypatch.setattr(seed_module, "SessionLocal", session_factory)
    seed_module.seed()
    seed_module.seed()  # second run must not fail or duplicate

    s = session_factory()
    try:
        user = s.query(User).filter(User.username == seed_module.DEMO_USERNAME).one()
        assert user.notification_settings == {}
        notes = s.query(Note).filter(Note.user_id == user.id).all()
        assert len(notes) == 5

        yesterday = [n for n in notes if n.title == "Yesterday retro"]
        assert len(yesterday) == 1
        assert yesterday[0].notification_status == {"telegram": "skipped"}
        assert s.query(NoteNotification).count() == 1

        dated_upcoming = [n for n in notes if n.note_date is not None and n.note_date >= TODAY]
        assert dated_upcoming
        for n in dated_upcoming:
            assert n.notification_status == {"telegram": "pending"}
    finally:
        s.close()
