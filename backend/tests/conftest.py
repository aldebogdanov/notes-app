import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("JWT_SECRET", "test-secret-with-at-least-32-characters")
# Hard assignments, not setdefault: the suite must behave identically no matter
# what the surrounding container exports (a token-enabled `make up` previously
# leaked TELEGRAM_BOT_TOKEN into `make test` and broke the tokenless tests).
# The reminder loop must not run against the test SQLite engine either —
# scheduler tests instantiate the class directly. Env vars also take precedence
# over backend/.env in pydantic-settings, so this pins both sources.
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["SCHEDULER_ENABLED"] = "false"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import deps
from app.db import Base
from app.main import app
from app.rate_limit import reset_auth_rate_limits


@pytest.fixture()
def client(tmp_path):
    reset_auth_rate_limits()
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False}, future=True)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[deps.get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    reset_auth_rate_limits()
    Base.metadata.drop_all(bind=engine)
