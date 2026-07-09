"""Tests for admin auth: password hashing, JWT round-trip, and the login route."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver


def test_password_hash_roundtrip():
    from tools.auth import hash_password, verify_password
    h = hash_password("pilotpass123")
    assert h != "pilotpass123"
    assert verify_password("pilotpass123", h) is True
    assert verify_password("wrong", h) is False


def test_jwt_roundtrip_carries_clinic():
    from tools.auth import create_token, decode_token
    token = create_token(user_id=7, clinic_id=3, role="admin")
    claims = decode_token(token)
    assert claims["clinic_id"] == 3
    assert claims["sub"] == "7"
    assert claims["role"] == "admin"


def test_decode_rejects_garbage():
    from tools.auth import decode_token
    assert decode_token("not-a-token") is None


@pytest.fixture
async def client():
    from agent.graph import build_graph
    from api.app import app

    graph = await build_graph(checkpointer=InMemorySaver())
    with (
        patch("api.app.build_graph", new_callable=AsyncMock, return_value=graph),
        patch("api.app.get_pool", new_callable=AsyncMock, return_value=MagicMock()),
        patch("api.app.close_pool", new_callable=AsyncMock),
        patch("api.app.send_pending_reminders", new_callable=AsyncMock, return_value=0),
    ):
        with TestClient(app) as c:
            yield c


def test_login_success_returns_token(client):
    from tools.auth import hash_password
    fake_user = {
        "id": 1, "clinic_id": 1, "email": "a@b.bd",
        "password_hash": hash_password("secret123"), "role": "admin",
    }
    # login() also loads the clinic to check for suspension — mock it so no real
    # DB is touched (an unmocked get_clinic hits Postgres and is flaky in-suite).
    fake_clinic = {"id": 1, "name": "Test Clinic", "status": "active"}
    with (
        patch("api.routes.auth.get_user_by_email", new_callable=AsyncMock, return_value=fake_user),
        patch("api.routes.auth.get_clinic", new_callable=AsyncMock, return_value=fake_clinic),
    ):
        r = client.post("/auth/login", json={"email": "a@b.bd", "password": "secret123"})
    assert r.status_code == 200
    body = r.json()
    assert body["clinic_id"] == 1
    assert body["access_token"]


def test_login_bad_password_401(client):
    from tools.auth import hash_password
    fake_user = {
        "id": 1, "clinic_id": 1, "email": "a@b.bd",
        "password_hash": hash_password("secret123"), "role": "admin",
    }
    with patch("api.routes.auth.get_user_by_email", new_callable=AsyncMock, return_value=fake_user):
        r = client.post("/auth/login", json={"email": "a@b.bd", "password": "nope"})
    assert r.status_code == 401


def test_login_unknown_user_401(client):
    with patch("api.routes.auth.get_user_by_email", new_callable=AsyncMock, return_value=None):
        r = client.post("/auth/login", json={"email": "x@y.bd", "password": "whatever"})
    assert r.status_code == 401
