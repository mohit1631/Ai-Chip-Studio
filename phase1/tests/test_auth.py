"""
tests/test_auth.py
--------------------
Covers the Sprint 5 auth flow: register, login, /auth/me, and the failure
paths (duplicate email, wrong password) that are easy to silently break
when refactoring app/deps.py or app/auth.py.
"""
from __future__ import annotations


def test_register_creates_user_with_free_tier(client):
    resp = client.post(
        "/auth/register", json={"email": "new@example.com", "password": "s3cret-pass"}
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["email"] == "new@example.com"
    assert body["tier"] == "free"
    assert "id" in body
    # Password must never be echoed back in any form.
    assert "password" not in body
    assert "hashed_password" not in body


def test_register_duplicate_email_rejected(client):
    payload = {"email": "dupe@example.com", "password": "s3cret-pass"}
    first = client.post("/auth/register", json=payload)
    assert first.status_code == 201

    second = client.post("/auth/register", json=payload)
    assert second.status_code == 400


def test_login_with_correct_credentials_returns_bearer_token(client):
    client.post("/auth/register", json={"email": "bob@example.com", "password": "hunter2-ish"})

    resp = client.post(
        "/auth/login", data={"username": "bob@example.com", "password": "hunter2-ish"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert isinstance(body["access_token"], str) and len(body["access_token"]) > 10


def test_login_with_wrong_password_returns_401(client):
    client.post("/auth/register", json={"email": "carol@example.com", "password": "right-pass"})

    resp = client.post(
        "/auth/login", data={"username": "carol@example.com", "password": "wrong-pass"}
    )
    assert resp.status_code == 401


def test_login_with_unknown_email_returns_401_not_404(client):
    # Same error for "no such user" and "wrong password" -- don't leak
    # which emails are registered via a different status code/message.
    resp = client.post(
        "/auth/login", data={"username": "ghost@example.com", "password": "whatever"}
    )
    assert resp.status_code == 401


def test_me_requires_valid_token(client):
    resp = client.get("/auth/me")
    assert resp.status_code == 401


def test_me_returns_current_user_with_valid_token(client, auth_headers):
    resp = client.get("/auth/me", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["email"] == "alice@example.com"


def test_me_rejects_garbage_token(client):
    resp = client.get("/auth/me", headers={"Authorization": "Bearer not-a-real-token"})
    assert resp.status_code == 401
