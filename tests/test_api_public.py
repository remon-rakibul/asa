"""Public (unauthenticated) landing-page endpoints.

/public/pricing feeds the marketing landing page. It must (1) require no auth,
and (2) mirror settings exactly so the page never drifts from real billing.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
async def client():
    from api.app import app

    with (
        patch("api.app.build_graph", new_callable=AsyncMock, return_value=MagicMock()),
        patch("api.app.get_pool", new_callable=AsyncMock, return_value=MagicMock()),
        patch("api.app.close_pool", new_callable=AsyncMock),
        patch("tools.reminders.send_pending_reminders", new_callable=AsyncMock, return_value=0),
    ):
        with TestClient(app) as c:
            yield c


def test_pricing_is_public_and_mirrors_settings(client):
    from config import settings

    res = client.get("/public/pricing")
    assert res.status_code == 200  # no Authorization header sent
    body = res.json()
    assert body == {
        "patient_subscription_fee": settings.patient_subscription_fee,
        "hospital_subscription_fee": settings.hospital_subscription_fee,
        "free_agent_bookings_per_month": settings.free_agent_bookings_per_month,
        "patient_trial_days": settings.patient_trial_days,
        "currency": "BDT",
        "credits_enabled": settings.credits_enabled,
        "default_credit_rate_bdt": settings.default_credit_rate_bdt,
    }


def test_pricing_tracks_config_override(client):
    """Change the fee in settings → the endpoint reflects it with no code edit."""
    with patch("api.routes.public.settings.patient_subscription_fee", 149):
        res = client.get("/public/pricing")
    assert res.status_code == 200
    assert res.json()["patient_subscription_fee"] == 149
