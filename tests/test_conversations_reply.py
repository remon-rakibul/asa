"""Staff-reply tenancy on the unified pt-acc{N}-platform thread.

A hospital's staff must not be able to inject messages into a patient's
cross-hospital conversation just because they can see it — only when they
have an OPEN escalation assigned to them, or the thread is CURRENTLY on
their hospital. sms/whatsapp replies stay clinic-scoped, unchanged.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

_SESSION = "pt-acc7-platform"


@pytest.fixture
def client():
    from api.app import app
    from api.deps import current_user

    with (
        patch("api.app.build_graph", new_callable=AsyncMock, return_value=MagicMock()),
        patch("api.app.get_pool", new_callable=AsyncMock, return_value=MagicMock()),
        patch("api.app.close_pool", new_callable=AsyncMock),
        patch("tools.reminders.send_pending_reminders", new_callable=AsyncMock, return_value=0),
    ):
        with TestClient(app) as c:
            yield c

    app.dependency_overrides.pop(current_user, None)


def _as(client, claims: dict):
    from api.app import app
    from api.deps import current_user

    app.dependency_overrides[current_user] = lambda: claims


def _mock_graph_state(client, hospital_id: int | None):
    graph = MagicMock()
    graph.aupdate_state = AsyncMock()
    snapshot = MagicMock()
    snapshot.values = {"hospital_id": hospital_id}
    graph.aget_state = AsyncMock(return_value=snapshot)
    client.app.state.graph = graph
    return graph


def test_reply_denied_without_escalation_or_matching_hospital(client):
    _as(client, {"user_id": 1, "role": "hospital_admin", "clinic_id": 2, "hospital_id": 10})
    _mock_graph_state(client, hospital_id=99)  # thread currently on a DIFFERENT hospital
    with patch("api.routes.conversations.escalation_open_for_staff",
               new=AsyncMock(return_value=False)):
        r = client.post(f"/conversations/{_SESSION}/reply", json={"text": "hi"})
    assert r.status_code == 403


def test_reply_allowed_with_open_escalation(client):
    _as(client, {"user_id": 1, "role": "hospital_admin", "clinic_id": 2, "hospital_id": 10})
    graph = _mock_graph_state(client, hospital_id=99)
    with (
        patch("api.routes.conversations.escalation_open_for_staff",
              new=AsyncMock(return_value=True)),
        patch("api.routes.conversations.log_turn", new=AsyncMock()),
    ):
        r = client.post(f"/conversations/{_SESSION}/reply", json={"text": "hi"})
    assert r.status_code == 200
    graph.aupdate_state.assert_awaited_once()


def test_reply_allowed_when_thread_currently_on_staff_hospital(client):
    _as(client, {"user_id": 1, "role": "hospital_admin", "clinic_id": 2, "hospital_id": 10})
    graph = _mock_graph_state(client, hospital_id=10)  # matches staff's own hospital
    with (
        patch("api.routes.conversations.escalation_open_for_staff",
              new=AsyncMock(return_value=False)),
        patch("api.routes.conversations.log_turn", new=AsyncMock()),
    ):
        r = client.post(f"/conversations/{_SESSION}/reply", json={"text": "hi"})
    assert r.status_code == 200
    graph.aupdate_state.assert_awaited_once()


def test_platform_admin_always_allowed(client):
    _as(client, {"user_id": 1, "role": "platform_admin"})
    graph = _mock_graph_state(client, hospital_id=99)
    with patch("api.routes.conversations.log_turn", new=AsyncMock()):
        r = client.post(f"/conversations/{_SESSION}/reply", json={"text": "hi"})
    assert r.status_code == 200
    graph.aupdate_state.assert_awaited_once()


def test_sms_reply_unchanged_clinic_scoped(client):
    _as(client, {"user_id": 1, "role": "hospital_admin", "clinic_id": 2, "hospital_id": 10})
    with (
        patch("api.routes.conversations.send_sms", new=AsyncMock()) as send,
        patch("api.routes.conversations.log_turn", new=AsyncMock()),
    ):
        r = client.post("/conversations/sms-01711000000/reply", json={"text": "hi"})
    assert r.status_code == 200
    send.assert_awaited_once()


def test_sms_reply_requires_clinic_scope(client):
    _as(client, {"user_id": 1, "role": "platform_admin"})  # no clinic_id
    r = client.post("/conversations/sms-01711000000/reply", json={"text": "hi"})
    assert r.status_code == 403
