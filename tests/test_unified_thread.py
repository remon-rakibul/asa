"""ONE unified LangGraph thread per patient account.

Chat and voice, from any entry point, converge on pt-acc{N}-platform: the
server derives the thread id from the JWT (client value ignored), every
patient turn runs platform-mode, and a clinic/doctor deep link is per-turn
state — not a different conversation. (Voice-side id unification is covered
in test_summarization.py.)
"""

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
    yield {"type": "end", "done": False}


def _spy_stream(captured):
    def spy(graph, session_id, message, **kwargs):
        captured.update(kwargs, session_id=session_id)
        return _fake_stream()
    return spy


def test_stream_ignores_client_session_id(client):
    """A hostile/stale client session id must not divert the thread."""
    captured = {}
    with (
        patch("api.routes.patient_portal.get_patient_account",
              new=AsyncMock(return_value=_ACCOUNT)),
        patch("api.routes.patient_portal.stream_turn_tokens", new=_spy_stream(captured)),
    ):
        r = client.post("/patient/chat/stream",
                        json={"session_id": "pt-acc999-platform", "message": "হাই"})
    assert r.status_code == 200
    assert captured["session_id"] == "pt-acc7-platform"
    assert captured["platform"] is True


def test_stream_session_id_optional(client):
    """New clients don't send session_id at all — the server derives it."""
    captured = {}
    with (
        patch("api.routes.patient_portal.get_patient_account",
              new=AsyncMock(return_value=_ACCOUNT)),
        patch("api.routes.patient_portal.stream_turn_tokens", new=_spy_stream(captured)),
    ):
        r = client.post("/patient/chat/stream", json={"message": "হাই"})
    assert r.status_code == 200
    assert captured["session_id"] == "pt-acc7-platform"


def test_prewarm_with_clinic_keeps_platform_head(client):
    """A doctor-page prewarm heats the SAME platform head the real turn uses,
    with the clinic/doctor pre-seeded so the {doctor_context} section matches."""
    with (
        patch("api.routes.patient_portal.get_patient_account",
              new=AsyncMock(return_value=_ACCOUNT)),
        patch("api.routes.patient_portal.get_hospital_id_for_clinic",
              new=AsyncMock(return_value=1)),
        patch("api.routes.patient_portal.get_doctor",
              new=AsyncMock(return_value={"id": 5, "name": "Rahim"})),
        patch("agent.nodes.prewarm_turn", new=AsyncMock()) as warm,
    ):
        r = client.post("/patient/chat/prewarm", json={"clinic_id": 2, "doctor_id": 5})
    assert r.status_code == 200
    state = warm.call_args.args[0]
    assert state["platform_mode"] is True
    assert state["clinic_id"] == 2 and state["doctor_id"] == 5
    assert state["hospital_id"] == 1
