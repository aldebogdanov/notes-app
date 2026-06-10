import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("JWT_SECRET", "test-secret-with-at-least-32-characters")
# TestClient runs the lifespan; the reminder loop must not run against the
# test SQLite engine. Scheduler tests instantiate the class directly.
os.environ.setdefault("SCHEDULER_ENABLED", "false")

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
