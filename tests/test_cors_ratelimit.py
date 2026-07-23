"""CORS must be the outermost middleware.

Regression for the ordering bug where CORSMiddleware sat INSIDE the rate
limiter: a 429 short-circuited by the limiter never passed through CORS, so a
browser saw an opaque network error instead of the 429 (and preflight OPTIONS
burned rate-limit tokens). CORS is now registered last (outermost).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

_ORIGIN = "http://localhost:3000"


@pytest.fixture
def client():
    with (
        patch("api.app.build_graph", new_callable=AsyncMock, return_value=MagicMock()),
        patch("api.app.get_pool", new_callable=AsyncMock, return_value=MagicMock()),
        patch("api.app.close_pool", new_callable=AsyncMock),
        patch("tools.reminders.send_pending_reminders", new_callable=AsyncMock, return_value=0),
    ):
        from api.app import app
        with TestClient(app) as c:
            yield c


def test_rate_limited_429_still_has_cors_header(client):
    # Mock the account lookup so the pre-limit attempts return 401 without a DB
    # — the test is about the 429's CORS header, not login itself.
    resp = None
    with patch(
        "api.routes.patient_portal.get_patient_account_by_email",
        new=AsyncMock(return_value=None),
    ):
        for _ in range(15):  # login limit is 10/min — this crosses it
            resp = client.post(
                "/patient/login",
                json={"email": "x@x.com", "password": "nope"},
                headers={"Origin": _ORIGIN},
            )
    assert resp.status_code == 429
    assert resp.headers.get("access-control-allow-origin") == _ORIGIN


def test_preflight_options_not_rate_limited(client):
    resp = client.options(
        "/patient/login",
        headers={"Origin": _ORIGIN, "Access-Control-Request-Method": "POST"},
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == _ORIGIN
