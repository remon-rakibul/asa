"""One-time phone verification (premium voice-calling gate).

API: POST /patient/phone/verify/{start,confirm} + /patient/me phone_verified.
Voice worker: caller-ID extraction and the platform-number premium gate.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

NOW = datetime.now(timezone.utc)


@pytest.fixture
async def client():
    from agent.graph import build_graph
    from api.app import app
    from api.deps import current_patient

    graph = await build_graph(checkpointer=InMemorySaver())
    app.dependency_overrides[current_patient] = lambda: {"account_id": 7}
    with (
        patch("api.app.build_graph", new_callable=AsyncMock, return_value=graph),
        patch("api.app.get_pool", new_callable=AsyncMock, return_value=MagicMock()),
        patch("api.app.close_pool", new_callable=AsyncMock),
        patch("tools.reminders.send_pending_reminders", new_callable=AsyncMock, return_value=0),
    ):
        with TestClient(app) as c:
            yield c
    app.dependency_overrides.pop(current_patient, None)


def _acct(**over):
    base = {"id": 7, "email": "k@a.com", "name": "Kodu", "phone": "01711000000",
            "created_at": NOW, "plan": "free", "premium_until": None,
            "trial_ends_at": None, "phone_verified_at": None}
    base.update(over)
    return base


def _pv(**over):
    from tools.auth import hash_password
    base = {"account_id": 7, "phone": "01711000000",
            "code_hash": hash_password("123456"),
            "expires_at": NOW + timedelta(minutes=10), "attempts": 0,
            "created_at": NOW - timedelta(minutes=2)}
    base.update(over)
    return base


# --- POST /patient/phone/verify/start ---------------------------------------

def _start_patches(**over):
    defaults = dict(
        get_patient_account=AsyncMock(return_value=_acct()),
        get_verified_account_by_phone=AsyncMock(return_value=None),
        get_phone_verification=AsyncMock(return_value=None),
        upsert_phone_verification=AsyncMock(),
        send_sms=AsyncMock(),
    )
    defaults.update(over)
    return [patch(f"api.routes.patient_portal.{name}", new=mock)
            for name, mock in defaults.items()]


def test_start_sends_code(client):
    upsert = AsyncMock()
    sms = AsyncMock()
    patches = _start_patches(upsert_phone_verification=upsert, send_sms=sms)
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        r = client.post("/patient/phone/verify/start", json={"phone": "+8801711000000"})
    assert r.status_code == 200
    # Normalized to local format before storing/sending.
    assert upsert.await_args.args[1] == "01711000000"
    assert sms.await_args.args[0] == "01711000000"
    code_in_sms = sms.await_args.args[1]
    assert any(ch.isdigit() for ch in code_in_sms)


def test_start_rejects_non_bd_number(client):
    patches = _start_patches()
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        r = client.post("/patient/phone/verify/start", json={"phone": "+15551234567"})
    assert r.status_code == 400


def test_start_409_when_already_verified(client):
    patches = _start_patches(
        get_patient_account=AsyncMock(return_value=_acct(phone_verified_at=NOW)))
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        r = client.post("/patient/phone/verify/start", json={"phone": "01711000000"})
    assert r.status_code == 409


def test_start_409_when_number_owned_by_other_account(client):
    patches = _start_patches(
        get_verified_account_by_phone=AsyncMock(return_value=_acct(id=99)))
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        r = client.post("/patient/phone/verify/start", json={"phone": "01711000000"})
    assert r.status_code == 409


def test_start_429_within_resend_cooldown(client):
    patches = _start_patches(
        get_phone_verification=AsyncMock(return_value=_pv(created_at=NOW)))
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        r = client.post("/patient/phone/verify/start", json={"phone": "01711000000"})
    assert r.status_code == 429


# --- POST /patient/phone/verify/confirm --------------------------------------

def test_confirm_ok_marks_verified(client):
    confirm = AsyncMock(return_value="ok")
    with (
        patch("api.routes.patient_portal.get_phone_verification",
              new=AsyncMock(return_value=_pv())),
        patch("api.routes.patient_portal.confirm_phone_verification", new=confirm),
    ):
        r = client.post("/patient/phone/verify/confirm", json={"code": "123456"})
    assert r.status_code == 200
    assert r.json()["phone_verified"] is True
    confirm.assert_awaited_once_with(7, "01711000000")


def test_confirm_wrong_code_increments_attempts(client):
    bump = AsyncMock(return_value=1)
    with (
        patch("api.routes.patient_portal.get_phone_verification",
              new=AsyncMock(return_value=_pv())),
        patch("api.routes.patient_portal.increment_phone_verification_attempts", new=bump),
    ):
        r = client.post("/patient/phone/verify/confirm", json={"code": "000000"})
    assert r.status_code == 400
    bump.assert_awaited_once()


def test_confirm_expired_code_rejected(client):
    with patch("api.routes.patient_portal.get_phone_verification",
               new=AsyncMock(return_value=_pv(expires_at=NOW - timedelta(minutes=1)))):
        r = client.post("/patient/phone/verify/confirm", json={"code": "123456"})
    assert r.status_code == 400


def test_confirm_locked_after_max_attempts(client):
    with patch("api.routes.patient_portal.get_phone_verification",
               new=AsyncMock(return_value=_pv(attempts=5))):
        r = client.post("/patient/phone/verify/confirm", json={"code": "123456"})
    assert r.status_code == 400


def test_confirm_409_when_number_taken_at_confirm_time(client):
    """Race: another account verified the same number between start and confirm
    — the partial unique index makes the UPDATE fail, surfaced as 409."""
    with (
        patch("api.routes.patient_portal.get_phone_verification",
              new=AsyncMock(return_value=_pv())),
        patch("api.routes.patient_portal.confirm_phone_verification",
              new=AsyncMock(return_value="phone_taken")),
    ):
        r = client.post("/patient/phone/verify/confirm", json={"code": "123456"})
    assert r.status_code == 409


# --- GET /patient/me exposes phone_verified ----------------------------------

def test_me_reports_phone_verified(client):
    with (
        patch("api.routes.patient_portal.get_patient_account",
              new=AsyncMock(return_value=_acct(phone_verified_at=NOW))),
        patch("api.routes.patient_portal.get_agent_bookings_used",
              new=AsyncMock(return_value=0)),
    ):
        r = client.get("/patient/me")
    assert r.status_code == 200
    assert r.json()["phone_verified"] is True


# --- tools.database.confirm_phone_verification --------------------------------

async def test_confirm_phone_verification_unique_violation_returns_taken():
    import asyncpg
    from tools.database import confirm_phone_verification

    conn = MagicMock()
    conn.execute = AsyncMock(
        side_effect=asyncpg.UniqueViolationError("uq_patient_accounts_verified_phone"))
    tx = MagicMock()
    tx.__aenter__ = AsyncMock(return_value=None)
    tx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    with patch("tools.database._pool", pool):
        assert await confirm_phone_verification(7, "01711000000") == "phone_taken"
