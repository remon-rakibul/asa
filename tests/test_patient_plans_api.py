"""Patient freemium API surface: GET /patient/me enrichment, the free-tier
history cap on GET /patient/appointments, and POST /patient/subscription/checkout."""

from __future__ import annotations

from contextlib import ExitStack
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
            "created_at": NOW, "plan": "free", "premium_until": None, "trial_ends_at": None}
    base.update(over)
    return base


def _appt(i, status="confirmed"):
    return {
        "id": f"apt-{i}", "hospital_id": 1, "hospital_name": "City", "clinic_id": 2,
        "department_name": "Cardiology", "doctor_id": 5, "doctor_name": "Rahim",
        "patient_name": "Kodu", "patient_mobile": "01711000000",
        "scheduled_at": NOW + timedelta(days=i), "duration_mins": 30, "status": status,
        "serial_number": i, "created_at": NOW, "payment_expires_at": None,
    }


# --- GET /patient/me -------------------------------------------------------

def test_me_free_tier_reports_cap(client):
    with (
        patch("api.routes.patient_portal.get_patient_account",
              new=AsyncMock(return_value=_acct())),
        patch("api.routes.patient_portal.get_agent_bookings_used",
              new=AsyncMock(return_value=2)),
        patch("api.routes.patient_portal.settings.free_agent_bookings_per_month", 3),
        patch("api.routes.patient_portal.settings.patient_subscription_fee", 99),
    ):
        r = client.get("/patient/me")
    assert r.status_code == 200
    body = r.json()
    assert body["tier"] == "free"
    assert body["agent_bookings_used"] == 2
    assert body["agent_bookings_cap"] == 3
    assert body["subscription_fee"] == 99


def test_me_trial_tier_uncapped(client):
    with (
        patch("api.routes.patient_portal.get_patient_account",
              new=AsyncMock(return_value=_acct(trial_ends_at=NOW + timedelta(days=10)))),
        patch("api.routes.patient_portal.get_agent_bookings_used",
              new=AsyncMock(return_value=0)),
    ):
        r = client.get("/patient/me")
    assert r.status_code == 200
    body = r.json()
    assert body["tier"] == "trial"
    assert body["agent_bookings_cap"] == -1  # unlimited


# --- GET /patient/appointments (history cap) -------------------------------

def test_appointments_free_tier_truncates_history(client):
    rows = [_appt(i) for i in range(6)]
    with (
        patch("api.routes.patient_portal.get_patient_account",
              new=AsyncMock(return_value=_acct())),
        patch("api.routes.patient_portal.list_appointments_for_account",
              new=AsyncMock(return_value=rows)),
        patch("api.routes.patient_portal.settings.free_history_limit", 3),
    ):
        r = client.get("/patient/appointments")
    assert r.status_code == 200
    body = r.json()
    assert body["truncated"] is True
    assert body["total"] == 6
    assert len(body["items"]) == 3


def test_appointments_premium_full_history(client):
    rows = [_appt(i) for i in range(6)]
    with (
        patch("api.routes.patient_portal.get_patient_account",
              new=AsyncMock(return_value=_acct(plan="premium", premium_until=NOW + timedelta(days=5)))),
        patch("api.routes.patient_portal.list_appointments_for_account",
              new=AsyncMock(return_value=rows)),
        patch("api.routes.patient_portal.settings.free_history_limit", 3),
    ):
        r = client.get("/patient/appointments")
    body = r.json()
    assert body["truncated"] is False
    assert len(body["items"]) == 6


# --- POST /patient/subscription/checkout -----------------------------------

def _provider(**init_result):
    p = MagicMock()
    p.initiate = AsyncMock(return_value=init_result)
    return p


def test_subscription_autopay_activates_premium(client):
    confirm = AsyncMock(return_value={"status": "ok", "appointment_id": None,
                                      "appointment": None, "kind": "patient_subscription"})
    # get_patient_account is called twice: pre-checkout, then post-activation.
    accounts = [_acct(), _acct(plan="premium", premium_until=NOW + timedelta(days=30))]
    with (
        patch("api.routes.patient_portal.get_patient_account",
              new=AsyncMock(side_effect=accounts)),
        patch("api.routes.patient_portal.create_payment",
              new=AsyncMock(return_value={"id": "pay-sub"})),
        patch("api.routes.patient_portal.get_provider", return_value=_provider(auto_paid=True)),
        patch("api.routes.patient_portal.confirm_paid_booking", new=confirm),
        patch("api.routes.patient_portal.settings.patient_subscription_fee", 99),
    ):
        r = client.post("/patient/subscription/checkout")
    assert r.status_code == 200
    body = r.json()
    assert body["tier"] == "premium"
    assert body["payment"]["pay_url"] is None  # auto-paid, nothing left to do
    confirm.assert_awaited_once()


def test_subscription_gateway_returns_pay_url(client):
    with (
        patch("api.routes.patient_portal.get_patient_account",
              new=AsyncMock(return_value=_acct())),
        patch("api.routes.patient_portal.create_payment",
              new=AsyncMock(return_value={"id": "pay-sub"})),
        patch("api.routes.patient_portal.get_provider",
              return_value=_provider(pay_url="https://gw.example/sub")),
        patch("api.routes.patient_portal.confirm_paid_booking", new=AsyncMock()) as confirm,
        patch("api.routes.patient_portal.settings.patient_subscription_fee", 99),
    ):
        r = client.post("/patient/subscription/checkout")
    assert r.status_code == 200
    body = r.json()
    assert body["payment"]["pay_url"] == "https://gw.example/sub"
    assert body["tier"] == "free"  # not premium until the IPN confirms
    confirm.assert_not_awaited()
