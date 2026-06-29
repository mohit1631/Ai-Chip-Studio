"""
tests/conftest.py
-------------------
Shared pytest fixtures: an isolated in-memory SQLite DB per test (so tests
never see each other's data or touch the real dev .db file), a FastAPI
TestClient wired to that DB via dependency override, and small helpers for
registering/logging in a user so individual tests don't repeat that
boilerplate.

Run with:
    AICHIP_CELERY_TASK_ALWAYS_EAGER=true pytest

(the eager flag matters for any test that touches a route which enqueues a
Celery task -- without it, the task is sent to Redis and never runs, and
the test would hang or get a stale "pending" status. None of the tests
here currently hit those routes, but it's the right default to set if you
extend this suite into code_fix/testbench/simulation.)
"""
from __future__ import annotations

import os

# Must be set before app.config is imported anywhere, including
# transitively via app.main -- pydantic-settings reads env vars at import
# time when Settings() is instantiated.
os.environ.setdefault("AICHIP_ENVIRONMENT", "development")
os.environ.setdefault("AICHIP_JWT_SECRET_KEY", "test-secret-not-for-real-use")
os.environ.setdefault("AICHIP_DATABASE_URL", "sqlite://")  # in-memory, per-engine
os.environ.setdefault("AICHIP_AUTO_CREATE_TABLES", "false")  # tests create tables themselves
os.environ.setdefault("AICHIP_CELERY_TASK_ALWAYS_EAGER", "true")
os.environ.setdefault("AICHIP_STORAGE_BACKEND", "local")
os.environ.setdefault("AICHIP_STORAGE_ROOT", "./test_storage")
os.environ.setdefault("AICHIP_RATE_LIMIT_DEFAULT", "1000/minute")  # generous unless a test overrides
os.environ.setdefault("AICHIP_RATE_LIMIT_LOGIN", "1000/minute")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture()
def db_engine():
    """Fresh in-memory SQLite engine per test, all tables created fresh."""
    from app.database import Base

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # single connection shared across threads/requests
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture()
def client(db_engine):
    """TestClient with get_db overridden to use the per-test in-memory DB."""
    from app.database import get_db
    from app.main import app

    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)

    def _override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def register_and_login(client):
    """Returns a helper: call it with (email, password) -> bearer access token."""

    def _do(email: str = "alice@example.com", password: str = "correct-horse-battery") -> str:
        resp = client.post("/auth/register", json={"email": email, "password": password})
        assert resp.status_code == 201, resp.text
        resp = client.post(
            "/auth/login",
            data={"username": email, "password": password},
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["access_token"]

    return _do


@pytest.fixture()
def auth_headers(register_and_login):
    """Convenience: a ready-to-use Authorization header for a fresh user."""
    token = register_and_login()
    return {"Authorization": f"Bearer {token}"}
