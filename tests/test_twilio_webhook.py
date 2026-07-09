"""Tests for the Twilio SMS webhook."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver


FAKE_RUN_TURN_RESULT = {
    "reply": "আসসালামু আলাইকুম! আপনার নাম কী?",
    "phase": "collect_info",
    "appointment_id": None,
    "patient_name": None,
    "done": False,
}

_SMS_FORM = "From=%2B8801711000000&Body=hello"
_SMS_HEADERS = {"Content-Type": "application/x-www-form-urlencoded"}


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


def _no_validate(request, form_data):
    """Replacement for _validate_signature that always passes."""


def test_sms_webhook_returns_empty_twiml(client):
    """The webhook returns empty TwiML immediately; the reply is delivered
    out-of-band via the SMS provider (run in the background task)."""
    sent = AsyncMock()
    with (
        patch("api.routes.twilio_sms._validate_signature", new=_no_validate),
        patch("api.routes.twilio_sms.run_turn", new_callable=AsyncMock,
              return_value=FAKE_RUN_TURN_RESULT),
        patch("api.routes.twilio_sms.send_sms", new=sent),
    ):
        r = client.post("/twilio/sms", content=_SMS_FORM, headers=_SMS_HEADERS)
    assert r.status_code == 200
    assert "application/xml" in r.headers["content-type"]
    assert "<Response></Response>" in r.text
    # Reply is sent to the patient via the provider, not embedded in the response.
    sent.assert_awaited_once()
    assert "আসসালামু আলাইকুম" in sent.call_args[0][1]


def test_sms_webhook_delivers_reply_to_patient(client):
    result = {**FAKE_RUN_TURN_RESULT, "reply": "আপনার অ্যাপয়েন্টমেন্ট হয়েছে।"}
    sent = AsyncMock()
    with (
        patch("api.routes.twilio_sms._validate_signature", new=_no_validate),
        patch("api.routes.twilio_sms.run_turn", new_callable=AsyncMock, return_value=result),
        patch("api.routes.twilio_sms.send_sms", new=sent),
    ):
        client.post("/twilio/sms",
                    content="From=%2B8801711000000&Body=test",
                    headers=_SMS_HEADERS)
    sent.assert_awaited_once()
    to, body = sent.call_args[0][0], sent.call_args[0][1]
    assert to == "+8801711000000"  # reply goes back to the patient (From)
    assert body == "আপনার অ্যাপয়েন্টমেন্ট হয়েছে।"


def test_sms_webhook_uses_phone_as_session_id(client):
    mock_run = AsyncMock(return_value=FAKE_RUN_TURN_RESULT)
    with (
        patch("api.routes.twilio_sms._validate_signature", new=_no_validate),
        patch("api.routes.twilio_sms.run_turn", new=mock_run),
    ):
        client.post("/twilio/sms", content=_SMS_FORM, headers=_SMS_HEADERS)
    # run_turn(graph, session_id, message) — session_id is arg[1]
    session_id = mock_run.call_args[0][1]
    assert "8801711000000" in session_id


def test_sms_webhook_rejects_invalid_signature(client):
    from config import settings
    original = settings.twilio_auth_token
    try:
        object.__setattr__(settings, "twilio_auth_token", "real-secret")
        r = client.post(
            "/twilio/sms",
            content=_SMS_FORM,
            headers={**_SMS_HEADERS, "X-Twilio-Signature": "badsig"},
        )
        assert r.status_code == 403
    finally:
        object.__setattr__(settings, "twilio_auth_token", original)
