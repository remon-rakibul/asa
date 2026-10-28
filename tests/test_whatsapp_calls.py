"""WhatsApp voice calls: webhook routing → LiveKit connector → voice-worker scope."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from tools import whatsapp_calls

CALLER = "8801700000123"
BUSINESS = "15556657143"


@pytest.fixture
def client():
    from api.app import app

    with (
        patch("api.app.build_graph", new_callable=AsyncMock, return_value=MagicMock()),
        patch("api.app.get_pool", new_callable=AsyncMock, return_value=MagicMock()),
        patch("api.app.close_pool", new_callable=AsyncMock),
        patch("api.app.send_pending_reminders", new_callable=AsyncMock, return_value=0),
        patch("api.app._outbox_loop", new_callable=AsyncMock),
    ):
        with TestClient(app) as c:
            yield c


def _call_payload(*calls):
    return {"entry": [{"changes": [{"field": "calls", "value": {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": BUSINESS, "phone_number_id": "1171444326050003"},
        "calls": list(calls),
    }}]}]}


def _connect(cid="wacid.1", direction="USER_INITIATED"):
    return {"id": cid, "from": CALLER, "to": BUSINESS, "event": "connect",
            "direction": direction, "session": {"sdp_type": "offer", "sdp": "v=0\r\n..."}}


# --------------------------------------------------------------------------- #
# Webhook routing
# --------------------------------------------------------------------------- #

def test_connect_event_is_accepted_with_number_metadata(client):
    accept, end = AsyncMock(), AsyncMock()
    with (
        patch("api.routes.whatsapp.seen_inbound", AsyncMock(return_value=False)),
        patch("api.routes.whatsapp.accept_inbound_call", accept),
        patch("api.routes.whatsapp.end_call", end),
        patch("api.routes.whatsapp._verify_meta_signature", lambda *a: None),
    ):
        r = client.post("/whatsapp/webhook", json=_call_payload(_connect()))
    assert r.json() == {"status": "ok"}
    call, phone_id, business = accept.await_args.args
    assert call["id"] == "wacid.1" and phone_id == "1171444326050003" and business == BUSINESS
    end.assert_not_awaited()


def test_terminate_event_releases_livekit_session(client):
    accept, end = AsyncMock(), AsyncMock()
    with (
        patch("api.routes.whatsapp.seen_inbound", AsyncMock(return_value=False)),
        patch("api.routes.whatsapp.accept_inbound_call", accept),
        patch("api.routes.whatsapp.end_call", end),
        patch("api.routes.whatsapp._verify_meta_signature", lambda *a: None),
    ):
        client.post("/whatsapp/webhook", json=_call_payload(
            {"id": "wacid.1", "from": CALLER, "event": "terminate", "status": "COMPLETED"}))
    end.assert_awaited_once_with("wacid.1")
    accept.assert_not_awaited()


def test_retried_connect_and_business_initiated_calls_are_ignored(client):
    accept = AsyncMock()
    seen = {"whatsapp_call": {"wacid.dup:connect"}}
    with (
        patch("api.routes.whatsapp.seen_inbound",
              AsyncMock(side_effect=lambda p, i: i in seen.get(p, set()))),
        patch("api.routes.whatsapp.accept_inbound_call", accept),
        patch("api.routes.whatsapp._verify_meta_signature", lambda *a: None),
    ):
        r = client.post("/whatsapp/webhook", json=_call_payload(
            _connect("wacid.dup"), _connect("wacid.out", direction="BUSINESS_INITIATED")))
    assert r.json() == {"status": "ignored"}
    accept.assert_not_awaited()


# --------------------------------------------------------------------------- #
# Connector + dispatch metadata
# --------------------------------------------------------------------------- #

def test_dispatch_metadata_for_verified_caller_joins_account_thread():
    scope = {"session_id": "pt-acc7-platform", "turn": {
        "channel": "whatsapp", "platform": True, "patient_account_id": 7,
        "patient_name": "রহিম", "patient_mobile": "01700000123"}}
    meta = whatsapp_calls.build_dispatch_metadata(scope, CALLER, "wacid.1", BUSINESS)
    assert meta["whatsapp_call"] and meta["platform"]
    assert meta["thread_id"] == "pt-acc7-platform" and meta["patient_account_id"] == 7
    assert meta["patient_phone"] == "01700000123" and meta["clinic_id"] is None


def test_dispatch_metadata_for_hospital_number_keeps_clinic():
    scope = {"session_id": f"whatsapp-{CALLER}", "turn": {"clinic_id": 4, "channel": "whatsapp"}}
    meta = whatsapp_calls.build_dispatch_metadata(scope, CALLER, "wacid.1", BUSINESS)
    assert meta["clinic_id"] == 4 and meta["platform"] is False
    # Anonymous caller: phone derived from the caller id so the agent can book.
    assert meta["patient_phone"] == "01700000123" and meta["patient_account_id"] is None


@pytest.fixture
def calling_on(monkeypatch):
    from config import settings

    for k, v in {"whatsapp_calling_enabled": True, "whatsapp_token": "tok",
                 "whatsapp_phone_id": "1171444326050003", "livekit_url": "wss://x.livekit.cloud",
                 "livekit_api_key": "k", "livekit_api_secret": "s"}.items():
        monkeypatch.setattr(settings, k, v)


@pytest.mark.asyncio
async def test_accept_inbound_call_dispatches_voice_agent(calling_on):
    lk = MagicMock()
    lk.connector.accept_whatsapp_call = AsyncMock(return_value=SimpleNamespace(room_name="wa-call-abc"))
    lk.aclose = AsyncMock()
    scope = {"session_id": f"whatsapp-{CALLER}", "turn": {"channel": "whatsapp", "platform": True}}
    with (
        patch("tools.whatsapp_calls._lkapi", return_value=lk),
        patch("api.messaging.resolve_message_scope", AsyncMock(return_value=scope)),
    ):
        room = await whatsapp_calls.accept_inbound_call(_connect(), "1171444326050003", BUSINESS)
    assert room == "wa-call-abc"
    req = lk.connector.accept_whatsapp_call.await_args.args[0]
    assert req.whatsapp_call_id == "wacid.1" and req.sdp.type == "offer"
    assert req.whatsapp_api_key == "tok" and CALLER not in req.room_name
    (dispatch,) = req.agents
    meta = json.loads(dispatch.metadata)
    assert meta["whatsapp_call"] and meta["thread_id"] == f"whatsapp-{CALLER}"
    lk.aclose.assert_awaited()


@pytest.mark.asyncio
async def test_accept_inbound_call_failure_never_raises(calling_on):
    lk = MagicMock()
    lk.connector.accept_whatsapp_call = AsyncMock(side_effect=RuntimeError("connector disabled"))
    lk.aclose = AsyncMock()
    with (
        patch("tools.whatsapp_calls._lkapi", return_value=lk),
        patch("api.messaging.resolve_message_scope",
              AsyncMock(return_value={"session_id": "s", "turn": {"platform": True}})),
    ):
        assert await whatsapp_calls.accept_inbound_call(_connect(), "", BUSINESS) is None
    lk.aclose.assert_awaited()


@pytest.mark.asyncio
async def test_accept_skipped_when_calling_not_configured(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "whatsapp_calling_enabled", False)
    with patch("tools.whatsapp_calls._lkapi") as lkapi:
        assert await whatsapp_calls.accept_inbound_call(_connect(), "", BUSINESS) is None
    lkapi.assert_not_called()


@pytest.mark.asyncio
async def test_end_call_is_user_initiated(calling_on):
    lk = MagicMock()
    lk.connector.disconnect_whatsapp_call = AsyncMock()
    lk.aclose = AsyncMock()
    with patch("tools.whatsapp_calls._lkapi", return_value=lk):
        await whatsapp_calls.end_call("wacid.1")
    req = lk.connector.disconnect_whatsapp_call.await_args.args[0]
    from livekit import api as lkapi_mod
    assert req.whatsapp_call_id == "wacid.1"
    assert req.disconnect_reason == lkapi_mod.DisconnectWhatsAppCallRequest.USER_INITIATED
