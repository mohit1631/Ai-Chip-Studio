"""
tests/test_security.py
------------------------
Tests for the two security fixes:
    1. app/config.py refuses to boot with AICHIP_ENVIRONMENT=production
       and the default dev JWT secret still in place.
    2. /auth/login is rate-limited (brute-force / credential-stuffing
       protection), separate from the general per-route default.

Test 1 constructs Settings() directly rather than going through the
client fixture, since the whole point is to check what happens *before*
the app would otherwise start -- importing app.main with a bad config is
exactly the scenario being guarded against.
"""
from __future__ import annotations

import importlib

import pytest


def test_production_with_default_secret_refuses_to_start(monkeypatch):
    monkeypatch.setenv("AICHIP_ENVIRONMENT", "production")
    monkeypatch.setenv("AICHIP_JWT_SECRET_KEY", "dev-secret")  # the insecure default

    import app.config as config_module

    importlib.reload(config_module)
    with pytest.raises(RuntimeError, match="refusing to boot"):
        config_module.Settings()


def test_production_with_real_secret_boots_fine(monkeypatch):
    monkeypatch.setenv("AICHIP_ENVIRONMENT", "production")
    monkeypatch.setenv("AICHIP_JWT_SECRET_KEY", "a-real-randomly-generated-secret-value")

    import app.config as config_module

    importlib.reload(config_module)
    settings = config_module.Settings()  # should not raise
    assert settings.environment == "production"


def test_development_with_default_secret_is_allowed(monkeypatch):
    # The dev default must stay convenient for local `uvicorn --reload`
    # use -- only production is guarded.
    monkeypatch.setenv("AICHIP_ENVIRONMENT", "development")
    monkeypatch.setenv("AICHIP_JWT_SECRET_KEY", "dev-secret")

    import app.config as config_module

    importlib.reload(config_module)
    settings = config_module.Settings()  # should not raise
    assert settings.jwt_secret_key == "dev-secret"


def test_login_endpoint_is_rate_limited(monkeypatch):
    # The shared `client` fixture (and conftest's env defaults) deliberately
    # use a generous rate limit so other tests aren't flaky. This test
    # needs the real, strict limit, so it builds its own app instance with
    # AICHIP_RATE_LIMIT_LOGIN set tight *before* app.rate_limit/app.main
    # are imported -- the Limiter reads settings at construction time.
    monkeypatch.setenv("AICHIP_RATE_LIMIT_LOGIN", "3/minute")
    monkeypatch.setenv("AICHIP_DATABASE_URL", "sqlite://")
    monkeypatch.setenv("AICHIP_AUTO_CREATE_TABLES", "true")

    import app.config as config_module
    import app.rate_limit as rate_limit_module
    import app.main as main_module

    importlib.reload(config_module)
    importlib.reload(rate_limit_module)
    importlib.reload(main_module)

    from fastapi.testclient import TestClient

    with TestClient(main_module.app) as strict_client:
        strict_client.post(
            "/auth/register",
            json={"email": "ratelimited@example.com", "password": "whatever-1"},
        )

        statuses = []
        for _ in range(10):
            resp = strict_client.post(
                "/auth/login",
                data={"username": "ratelimited@example.com", "password": "wrong-on-purpose"},
            )
            statuses.append(resp.status_code)

        assert 429 in statuses, (
            "expected /auth/login to start returning 429 after a few attempts "
            f"with AICHIP_RATE_LIMIT_LOGIN=3/minute, got statuses={statuses}"
        )
