"""POST /hospitals/signup — public hospital admin self-signup + free trial.

Provisioning is atomic (tools.database.create_hospital_tenant runs hospital +
clinic + channel + admin + trial in one transaction), so these tests mock that
single function rather than the four legacy per-table writes it replaced.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest
from fastapi.testclient import TestClient

_BODY = {
    "slug": "city-hospital",
    "name": "City Hospital",
    "doctor_name": "Dr. Rahim",
    "doctor_phone": "01711000000",
    "admin_email": "admin@cityhospital.bd",
    "admin_password": "Str0ngPass!",
}

_TENANT = {
    "hospital": {"id": 5, "slug": "city-hospital", "name": "City Hospital"},
    "clinic": {"id": 9, "slug": "city-hospital", "name": "City Hospital", "hospital_id": 5},
    "user": {"id": 3, "clinic_id": 9, "hospital_id": 5,
             "email": "admin@cityhospital.bd", "role": "hospital_admin"},
}


@pytest.fixture
def client():
    from api.app import app

    with (
        patch("api.app.build_graph", new_callable=AsyncMock, return_value=MagicMock()),
        patch("api.app.get_pool", new_callable=AsyncMock, return_value=MagicMock()),
        patch("api.app.close_pool", new_callable=AsyncMock),
        patch("tools.reminders.send_pending_reminders", new_callable=AsyncMock, return_value=0),
    ):
        with TestClient(app) as c:
            yield c


def test_signup_creates_tenant_atomically_with_trial(client):
    with (
        patch("api.routes.auth.get_user_by_email", new=AsyncMock(return_value=None)),
        patch("api.routes.auth.create_hospital_tenant",
              new=AsyncMock(return_value=_TENANT)) as create,
    ):
        r = client.post("/hospitals/signup", json=_BODY)

    assert r.status_code == 201
    body = r.json()
    assert body["access_token"] and body["clinic_id"] == 9
    # Self-signup requests the free trial (trial_days set, positive monthly fee).
    create.assert_awaited_once()
    assert create.await_args.kwargs["trial_days"] == 30
    assert create.await_args.kwargs["monthly_fee"] > 0


def test_signup_rejects_duplicate_slug(client):
    with (
        patch("api.routes.auth.get_user_by_email", new=AsyncMock(return_value=None)),
        patch("api.routes.auth.create_hospital_tenant",
              new=AsyncMock(side_effect=asyncpg.UniqueViolationError("slug taken"))),
    ):
        r = client.post("/hospitals/signup", json=_BODY)
    assert r.status_code == 409


def test_signup_rejects_duplicate_admin_email_upfront(client):
    # Pre-check catches it before any write — the tenant creator is never called.
    with (
        patch("api.routes.auth.get_user_by_email",
              new=AsyncMock(return_value={"id": 1})),
        patch("api.routes.auth.create_hospital_tenant", new=AsyncMock()) as create,
    ):
        r = client.post("/hospitals/signup", json=_BODY)
    assert r.status_code == 409
    create.assert_not_awaited()


def test_signup_weak_password_rejected_by_schema(client):
    r = client.post("/hospitals/signup", json={**_BODY, "admin_password": "weak"})
    assert r.status_code == 422


def test_signup_no_platform_key_required(client):
    """Unlike POST /clinics, this route must NOT require X-Platform-Key."""
    with (
        patch("api.routes.auth.get_user_by_email", new=AsyncMock(return_value=None)),
        patch("api.routes.auth.create_hospital_tenant",
              new=AsyncMock(return_value=_TENANT)),
    ):
        # No X-Platform-Key header sent at all.
        r = client.post("/hospitals/signup", json=_BODY)
    assert r.status_code == 201
