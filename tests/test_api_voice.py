"""Tests for the patient-portal voice token endpoint (POST /patient/voice/token)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver


@pytest.fixture
async def client():
    from agent.graph import build_graph
    from api.app import app
    from api.deps import current_patient

    graph = await build_graph(checkpointer=InMemorySaver())
    # Bypass patient JWT auth — act as account 1.
    app.dependency_overrides[current_patient] = lambda: {"account_id": 1}

    with (
        patch("api.app.build_graph", new_callable=AsyncMock, return_value=graph),
        patch("api.app.get_pool", new_callable=AsyncMock, return_value=MagicMock()),
        patch("api.app.close_pool", new_callable=AsyncMock),
        patch("api.app.send_pending_reminders", new_callable=AsyncMock, return_value=0),
    ):
        with TestClient(app) as c:
            yield c

    app.dependency_overrides.pop(current_patient, None)


_ACCOUNT = {"id": 1, "name": "রাহেলা বেগম", "phone": "01711000000"}


def _dispatch(token: str) -> dict:
    """Decode the (unverified) JWT and return the first agent dispatch entry."""
    claims = pyjwt.decode(token, options={"verify_signature": False})
    return claims["roomConfig"]["agents"][0]


def test_voice_token_clinic_level(client):
    with (
        patch("api.routes.voice.get_patient_account", new_callable=AsyncMock, return_value=_ACCOUNT),
        patch("api.routes.voice.get_hospital_id_for_clinic", new_callable=AsyncMock, return_value=7),
        patch("api.routes.voice.get_or_create_patient", new_callable=AsyncMock,
              return_value={"id": 99}) as goc,
    ):
        r = client.post("/patient/voice/token", json={"clinic_id": 3})

    assert r.status_code == 200
    body = r.json()
    assert body["room_name"].startswith("portal-voice-")
    assert body["server_url"]
    goc.assert_awaited_once()

    agent = _dispatch(body["participant_token"])
    assert agent["agentName"] == "appointment-setter"
    meta = json.loads(agent["metadata"])
    assert meta["clinic_id"] == 3
    assert meta["hospital_id"] == 7
    assert meta["patient_account_id"] == 1
    assert meta["patient_id"] == 99
    assert meta["patient_name"] == "রাহেলা বেগম"


def test_voice_token_hospital_level(client):
    # Hospital-level call: no department chosen → no patient record created.
    with (
        patch("api.routes.voice.get_patient_account", new_callable=AsyncMock, return_value=_ACCOUNT),
        patch("api.routes.voice.get_or_create_patient", new_callable=AsyncMock) as goc,
    ):
        r = client.post("/patient/voice/token", json={"hospital_id": 7})

    assert r.status_code == 200
    goc.assert_not_awaited()
    meta = json.loads(_dispatch(r.json()["participant_token"])["metadata"])
    assert meta["hospital_id"] == 7
    assert "clinic_id" not in meta


def test_voice_token_carries_preselected_doctor(client):
    with (
        patch("api.routes.voice.get_patient_account", new_callable=AsyncMock, return_value=_ACCOUNT),
        patch("api.routes.voice.get_hospital_id_for_clinic", new_callable=AsyncMock, return_value=7),
        patch("api.routes.voice.get_or_create_patient", new_callable=AsyncMock, return_value={"id": 99}),
        patch("api.routes.voice.get_doctor", new_callable=AsyncMock, return_value={"id": 5}) as gd,
    ):
        r = client.post("/patient/voice/token", json={"clinic_id": 3, "doctor_id": 5})

    assert r.status_code == 200
    gd.assert_awaited_once_with(5, clinic_id=3)
    meta = json.loads(_dispatch(r.json()["participant_token"])["metadata"])
    assert meta["doctor_id"] == 5


def test_voice_token_drops_mismatched_doctor(client):
    # A doctor from another clinic is silently dropped, same as /chat/stream.
    with (
        patch("api.routes.voice.get_patient_account", new_callable=AsyncMock, return_value=_ACCOUNT),
        patch("api.routes.voice.get_hospital_id_for_clinic", new_callable=AsyncMock, return_value=7),
        patch("api.routes.voice.get_or_create_patient", new_callable=AsyncMock, return_value={"id": 99}),
        patch("api.routes.voice.get_doctor", new_callable=AsyncMock, return_value=None),
    ):
        r = client.post("/patient/voice/token", json={"clinic_id": 3, "doctor_id": 999})

    assert r.status_code == 200
    meta = json.loads(_dispatch(r.json()["participant_token"])["metadata"])
    assert "doctor_id" not in meta


def test_voice_token_requires_scope(client):
    with patch("api.routes.voice.get_patient_account", new_callable=AsyncMock, return_value=_ACCOUNT):
        r = client.post("/patient/voice/token", json={})
    assert r.status_code == 400


def test_voice_token_unknown_clinic_404(client):
    with (
        patch("api.routes.voice.get_patient_account", new_callable=AsyncMock, return_value=_ACCOUNT),
        patch("api.routes.voice.get_hospital_id_for_clinic", new_callable=AsyncMock, return_value=None),
    ):
        r = client.post("/patient/voice/token", json={"clinic_id": 999})
    assert r.status_code == 404
