"""Tests for /appointments and /availability REST endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver


SAMPLE_APPOINTMENT = {
    "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    "patient_name": "রাহেলা বেগম",
    "patient_age": 42,
    "patient_mobile": "01711000000",
    "scheduled_at": datetime(2099, 1, 6, 9, 0, tzinfo=timezone.utc),
    "duration_mins": 30,
    "status": "confirmed",
    "created_at": datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
}


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


def test_get_appointments_empty(client):
    with patch("api.routes.appointments.list_appointments", new_callable=AsyncMock, return_value=[]):
        r = client.get("/appointments")
    assert r.status_code == 200
    assert r.json() == []


def test_get_appointments_returns_rows(client):
    with patch("api.routes.appointments.list_appointments", new_callable=AsyncMock,
               return_value=[SAMPLE_APPOINTMENT]):
        r = client.get("/appointments")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["patient_name"] == "রাহেলা বেগম"


def test_get_appointments_passes_filters(client):
    mock = AsyncMock(return_value=[])
    with patch("api.routes.appointments.list_appointments", new=mock):
        client.get("/appointments?status=confirmed&q=রাহেলা&date_from=2099-01-01&date_to=2099-12-31")
    mock.assert_awaited_once_with(
        clinic_id=1,
        date_from="2099-01-01",
        date_to="2099-12-31",
        status="confirmed",
        q="রাহেলা",
    )


def test_patch_appointment_cancel_success(client):
    with patch("api.routes.appointments.cancel_appointment", new_callable=AsyncMock, return_value=True):
        r = client.patch(
            "/appointments/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            json={"status": "cancelled"},
        )
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_patch_appointment_not_found(client):
    with patch("api.routes.appointments.cancel_appointment", new_callable=AsyncMock, return_value=False):
        r = client.patch(
            "/appointments/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            json={"status": "cancelled"},
        )
    assert r.status_code == 404


def test_get_availability(client):
    fake_slots = [{"datetime": "2099-01-06T09:00:00+00:00", "label": "সোমবার সকাল ৯টা"}]
    with patch("api.routes.appointments.get_available_slots", new_callable=AsyncMock,
               return_value=fake_slots):
        r = client.get("/availability?days_ahead=3")
    assert r.status_code == 200
    assert len(r.json()) == 1


_AID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def test_update_status_success(client):
    appt = {**SAMPLE_APPOINTMENT, "status": "checked_in"}
    with patch("api.routes.appointments.set_appointment_status", new_callable=AsyncMock,
               return_value={"status": "ok", "appointment": appt, "from": "confirmed"}):
        r = client.post(f"/appointments/{_AID}/status", json={"status": "checked_in"})
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_update_status_invalid_transition_409(client):
    with patch("api.routes.appointments.set_appointment_status", new_callable=AsyncMock,
               return_value={"status": "invalid", "appointment": None, "from": "completed"}):
        r = client.post(f"/appointments/{_AID}/status", json={"status": "checked_in"})
    assert r.status_code == 409


def test_update_status_not_found_404(client):
    with patch("api.routes.appointments.set_appointment_status", new_callable=AsyncMock,
               return_value={"status": "not_found", "appointment": None, "from": None}):
        r = client.post(f"/appointments/{_AID}/status", json={"status": "completed"})
    assert r.status_code == 404


def test_update_status_rejects_unknown_status(client):
    # Schema Literal rejects statuses outside the lifecycle set.
    r = client.post(f"/appointments/{_AID}/status", json={"status": "banana"})
    assert r.status_code == 422


def test_appointment_events_returns_timeline(client):
    events = [{
        "id": 1, "event_type": "created", "from_status": None, "to_status": "confirmed",
        "from_time": None, "to_time": None, "actor_user_id": None, "actor_role": "agent",
        "actor_email": None, "note": None,
        "created_at": datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
    }]
    with patch("api.routes.appointments.list_appointment_events", new_callable=AsyncMock,
               return_value=events):
        r = client.get(f"/appointments/{_AID}/events")
    assert r.status_code == 200
    assert r.json()[0]["event_type"] == "created"


def test_reports_summary(client):
    appt_stats = {
        "total": 10, "status_counts": {"completed": 6, "no_show": 2, "cancelled": 2},
        "completed": 6, "no_show": 2, "cancelled": 2,
        "no_show_rate": 25.0, "completion_rate": 75.0,
        "per_doctor": [{"doctor_id": 3, "name": "Dr A", "count": 8}],
        "daily": [{"day": "2026-06-01", "count": 10}],
    }
    sms = {"by_status": {"sent": 4, "failed": 1}, "by_kind": [{"kind": "reminder", "count": 5}]}
    with (
        patch("api.routes.reports.appointment_stats", new_callable=AsyncMock, return_value=appt_stats),
        patch("api.routes.reports.sms_stats", new_callable=AsyncMock, return_value=sms),
        patch("api.routes.reports.get_channel_stats", new_callable=AsyncMock, return_value=[]),
    ):
        r = client.get("/reports/summary?date_from=2026-06-01&date_to=2026-06-30")
    assert r.status_code == 200
    body = r.json()
    assert body["appointments"]["no_show_rate"] == 25.0
    assert body["sms"]["by_status"]["sent"] == 4


def test_reports_summary_rejects_bad_range(client):
    r = client.get("/reports/summary?date_from=2026-06-30&date_to=2026-06-01")
    assert r.status_code == 400
