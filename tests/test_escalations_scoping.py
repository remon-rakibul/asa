"""GET/PATCH /escalations scoping: hospital_admin sees their whole hospital
(own clinics + hospital-level rows raised before a department was chosen),
department-level roles stay clinic-scoped."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


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


def test_hospital_admin_lists_by_hospital(client):
    _as(client, {"user_id": 1, "role": "hospital_admin", "clinic_id": 2, "hospital_id": 10})
    with patch("api.routes.escalations.list_escalations",
               new=AsyncMock(return_value=[])) as mock:
        r = client.get("/escalations")
    assert r.status_code == 200
    assert mock.call_args.kwargs == {"hospital_id": 10, "status": "open"}


def test_dept_role_lists_by_clinic(client):
    _as(client, {"user_id": 1, "role": "dept_head", "clinic_id": 2, "hospital_id": 10})
    with patch("api.routes.escalations.list_escalations",
               new=AsyncMock(return_value=[])) as mock:
        r = client.get("/escalations")
    assert r.status_code == 200
    assert mock.call_args.kwargs == {"clinic_id": 2, "status": "open"}


def test_unscoped_role_403(client):
    _as(client, {"user_id": 1, "role": "platform_admin"})
    r = client.get("/escalations")
    assert r.status_code == 403


def test_resolve_uses_hospital_scope_for_hospital_admin(client):
    _as(client, {"user_id": 1, "role": "hospital_admin", "clinic_id": 2, "hospital_id": 10})
    with patch("api.routes.escalations.resolve_escalation",
               new=AsyncMock(return_value=True)) as mock:
        r = client.patch("/escalations/5")
    assert r.status_code == 200
    assert mock.call_args.args == (5,)
    assert mock.call_args.kwargs == {"hospital_id": 10}
