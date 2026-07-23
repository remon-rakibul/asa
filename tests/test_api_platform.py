"""Platform-wide assistant API surface: /patient/chat/stream with no
clinic_id, the literal /chat/history/platform routes, the platform prewarm
branch, and the marketplace search/specialties endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

_ACCOUNT = {"id": 7, "name": "Kodu", "phone": "01711000000"}


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


async def _fake_stream(*args, **kwargs):
    yield {"type": "token", "text": "হ্যালো"}
    yield {"type": "end", "done": False}


def test_platform_stream_omits_clinic_and_sets_platform_flag(client):
    captured = {}

    def spy(*args, **kwargs):
        captured.update(kwargs)
        return _fake_stream()

    with (
        patch("api.routes.patient_portal.get_patient_account",
              new=AsyncMock(return_value=_ACCOUNT)),
        patch("api.routes.patient_portal.get_or_create_patient",
              new=AsyncMock()) as goc,
        patch("api.routes.patient_portal.stream_turn_tokens", new=spy),
    ):
        r = client.post("/patient/chat/stream",
                        json={"session_id": "pt-acc7-platform", "message": "হাই"})

    assert r.status_code == 200
    # No clinic yet → no hospital patient record is created up front.
    goc.assert_not_awaited()
    assert captured["platform"] is True
    assert captured["clinic_id"] is None
    assert captured["hospital_id"] is None
    assert captured["patient_id"] is None
    assert captured["patient_account_id"] == 7


def test_clinic_stream_joins_unified_platform_thread(client):
    """A clinic deep-link is per-turn context on the ONE unified thread:
    platform mode stays on, the session id is derived server-side (a stale
    client-sent per-clinic id is ignored), and the hospital's patient record
    is still created up front."""
    captured = {}

    def spy(graph, session_id, message, **kwargs):
        captured.update(kwargs, session_id=session_id)
        return _fake_stream()

    with (
        patch("api.routes.patient_portal.get_patient_account",
              new=AsyncMock(return_value=_ACCOUNT)),
        patch("api.routes.patient_portal.get_hospital_id_for_clinic",
              new=AsyncMock(return_value=1)),
        patch("api.routes.patient_portal.get_or_create_patient",
              new=AsyncMock(return_value={"id": 99})) as goc,
        patch("api.routes.patient_portal.stream_turn_tokens", new=spy),
    ):
        r = client.post("/patient/chat/stream",
                        json={"session_id": "pt-acc7-clinic2", "message": "হাই",
                              "clinic_id": 2})

    assert r.status_code == 200
    goc.assert_awaited_once()
    assert captured["platform"] is True
    assert captured["session_id"] == "pt-acc7-platform"
    assert captured["clinic_id"] == 2
    assert captured["patient_id"] == 99


def test_platform_history_routes_use_platform_thread(client):
    # The literal /platform route must win over the int-typed /{clinic_id}.
    with patch("api.routes.patient_portal._history_for_thread",
               new=AsyncMock(return_value=[])) as hist:
        r = client.get("/patient/chat/history/platform")
    assert r.status_code == 200 and r.json() == []
    assert hist.await_args.args[1] == "pt-acc7-platform"


def test_platform_history_delete_clears_platform_thread(client):
    with patch("api.routes.patient_portal._clear_thread",
               new=AsyncMock()) as clear:
        r = client.delete("/patient/chat/history/platform")
    assert r.status_code == 200
    assert clear.await_args.args[1] == "pt-acc7-platform"
    assert clear.await_args.args[2] is None


def test_prewarm_without_clinic_warms_platform_prompt(client):
    with (
        patch("api.routes.patient_portal.get_patient_account",
              new=AsyncMock(return_value=_ACCOUNT)),
        patch("agent.nodes.prewarm_turn", new=AsyncMock()) as warm,
    ):
        r = client.post("/patient/chat/prewarm", json={})
    assert r.status_code == 200 and r.json() == {"ok": True}
    state = warm.call_args.args[0]
    assert state["platform_mode"] is True
    assert state["patient_account_id"] == 7
    assert "clinic_id" not in state    # platform head, not a department head


def test_search_endpoint_passes_filters(client):
    search = AsyncMock(return_value=[])
    with patch("api.routes.patient_portal.search_doctors_platform", new=search):
        r = client.get(
            "/patient/doctors/search",
            params={"q": "cardio", "specialty": "Cardiology", "hospital_id": 3,
                    "max_fee": 900, "sort": "fee", "page": 1},
        )
    assert r.status_code == 200 and r.json() == []
    kwargs = search.await_args.kwargs
    assert kwargs["q"] == "cardio"
    assert kwargs["specialty"] == "Cardiology"
    assert kwargs["hospital_id"] == 3
    assert kwargs["max_fee"] == 900
    assert kwargs["sort"] == "fee"
    assert kwargs["offset"] == 20      # page 1 → second page of 20


def test_search_available_sort_orders_by_next_slot(client):
    rows = [
        {"id": 1, "clinic_id": 1, "name": "A", "department_name": "D",
         "hospital_id": 1, "hospital_name": "H"},
        {"id": 2, "clinic_id": 1, "name": "B", "department_name": "D",
         "hospital_id": 1, "hospital_name": "H"},
        {"id": 3, "clinic_id": 1, "name": "C", "department_name": "D",
         "hospital_id": 1, "hospital_name": "H"},
    ]

    async def fake_slots(clinic_id, days_ahead=7, limit=1, doctor_id=None):
        return {
            1: [],  # no slot → must sort last
            2: [{"label": "মঙ্গলবার", "datetime": "2026-07-14T09:00:00"}],
            3: [{"label": "সোমবার", "datetime": "2026-07-13T09:00:00"}],
        }[doctor_id]

    with (
        patch("api.routes.patient_portal.search_doctors_platform",
              new=AsyncMock(return_value=rows)),
        patch("api.routes.patient_portal.get_available_slots", new=fake_slots),
    ):
        r = client.get("/patient/doctors/search", params={"sort": "available"})

    assert r.status_code == 200
    assert [d["id"] for d in r.json()] == [3, 2, 1]


def test_specialties_endpoint(client):
    with patch("api.routes.patient_portal.list_specialties",
               new=AsyncMock(return_value=[{"specialty": "Cardiology", "doctor_count": 4}])):
        r = client.get("/patient/specialties")
    assert r.status_code == 200
    assert r.json() == [{"specialty": "Cardiology", "doctor_count": 4}]


def test_doctor_detail_includes_slot_preview(client):
    row = {
        "id": 5, "clinic_id": 2, "name": "Rahim", "specialty": "Cardiology",
        "degrees": "MBBS", "description": "", "has_photo": False,
        "fee_new": 800, "fee_followup": None, "department_name": "Cardiology",
        "hospital_id": 1, "hospital_name": "City Hospital",
        "avg_rating": 4.5, "review_count": 12,
    }
    slots = [{"label": "সোমবার সকাল ৯টা", "datetime": "2026-07-13T09:00:00"}]
    with (
        patch("api.routes.patient_portal.get_doctor_public",
              new=AsyncMock(return_value=dict(row))),
        patch("api.routes.patient_portal.get_available_slots",
              new=AsyncMock(return_value=slots)),
    ):
        r = client.get("/patient/doctors/5")
    assert r.status_code == 200
    body = r.json()
    assert body["fee_new"] == 800 and body["fee_followup"] is None
    assert body["slots"] == slots and body["next_slot"] == slots[0]


def test_doctor_detail_404(client):
    with patch("api.routes.patient_portal.get_doctor_public",
               new=AsyncMock(return_value=None)):
        assert client.get("/patient/doctors/999").status_code == 404
