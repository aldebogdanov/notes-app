"""Seed a demo user with a few notes. Idempotent: wipes the demo user before inserting."""

from datetime import date, timedelta

from app.auth import hash_password
from app.db import SessionLocal
from app.models import Note, NoteNotification, User
from app.notifications import NotificationStatus

DEMO_USERNAME = "demo"
DEMO_PASSWORD = "demo1234"


def seed() -> None:
    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.username == DEMO_USERNAME).one_or_none()
        if existing:
            db.delete(existing)
            db.commit()

        user = User(
            username=DEMO_USERNAME,
            password_hash=hash_password(DEMO_PASSWORD),
            notification_settings={},
        )
        db.add(user)
        db.flush()

        today = date.today()
        notes: list[Note] = [
            Note(
                user_id=user.id,
                title="Welcome",
                content="# Welcome\n\nThis is your first note. Edit me, or create new ones.",
                tags=["intro"],
                note_date=None,
            ),
            Note(
                user_id=user.id,
                title="Grocery list",
                content="- milk\n- bread\n- eggs",
                tags=["todo", "shopping"],
                note_date=today,
            ),
            Note(
                user_id=user.id,
                title="Project kickoff",
                content="Meeting with the team **tomorrow**. Prepare the agenda.",
                tags=["work"],
                note_date=today + timedelta(days=1),
            ),
            Note(
                user_id=user.id,
                title="Book idea",
                content="_Fleeting thought worth keeping._",
                tags=["ideas"],
                note_date=None,
            ),
            # Past-dated note: reminder window already gone, so it carries the
            # same "skipped" row the 0003 migration backfills for old data.
            Note(
                user_id=user.id,
                title="Yesterday retro",
                content="What went well, what to improve.",
                tags=["work"],
                note_date=today - timedelta(days=1),
                notifications=[
                    NoteNotification(
                        channel="telegram",
                        status=NotificationStatus.SKIPPED,
                    )
                ],
            ),
        ]
        db.add_all(notes)
        db.commit()
        print(f"Seeded '{DEMO_USERNAME}' / '{DEMO_PASSWORD}' with {len(notes)} notes.")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
