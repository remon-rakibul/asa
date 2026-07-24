"""Wallet HTTP surface: the hospital-facing wallet + top-up, and the superadmin
wallet controls (rate, grant). Role gating matters most here — money control is
the platform_admin's alone, and a hospital_admin sees only their own wallet.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

NOW = datetime.now(timezone.utc)

_HOSPITAL_ADMIN = {"user_id": 2, "role": "hospital_admin", "clinic_id": 2, "hospital_id": 10}
_OTHER_HOSPITAL_ADMIN = {"user_id": 3, "role": "hospital_admin", "clinic_id": 5, "hospital_id": 99}
_PLATFORM_ADMIN = {"user_id": 1, "role": "platform_admin"}

_WALLET = {"hospital_id": 10, "balance": 40, "credit_rate_bdt": 20.0,
           "created_at": NOW, "updated_at": NOW}
_LEDGER = [{"id": 1, "delta": 50, "balance_after": 50, "reason": "topup", "quantity": 1,
            "clinic_id": None, "appointment_id": None, "payment_id": "p1", "note": "buy",
            "created_at": NOW}]


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
    from api.deps import current_user
    app.dependency_overrides.pop(current_user, None)


def _as(claims: dict):
    from api.app import app
    from api.deps import current_user
    app.dependency_overrides[current_user] = lambda: claims


# --- hospital-facing wallet ------------------------------------------------

def test_get_wallet_requires_hospital_admin(client):
    _as({"user_id": 9, "role": "platform_admin"})  # no hospital scope
    assert client.get("/hospital/wallet").status_code == 403


def test_get_wallet_returns_balance_and_ledger(client):
    _as(_HOSPITAL_ADMIN)
    with (
        patch("api.routes.wallet.get_or_create_hospital_wallet", new=AsyncMock(return_value=_WALLET)),
        patch("api.routes.wallet.list_wallet_ledger", new=AsyncMock(return_value=_LEDGER)),
    ):
        r = client.get("/hospital/wallet")
    assert r.status_code == 200
    body = r.json()
    assert body["balance"] == 40
    assert body["credit_rate_bdt"] == 20.0
    assert body["low_balance"] is False
    assert body["ledger"][0]["reason"] == "topup"


def test_topup_prices_at_hospital_rate_and_creates_credit_topup(client):
    _as(_HOSPITAL_ADMIN)
    provider = MagicMock()
    provider.initiate = AsyncMock(return_value={"pay_url": "http://pay/x"})
    with (
        patch("api.routes.wallet.get_or_create_hospital_wallet", new=AsyncMock(return_value=_WALLET)),
        patch("api.routes.wallet.get_hospital", new=AsyncMock(return_value={"name": "City"})),
        patch("api.routes.wallet.create_payment",
              new=AsyncMock(return_value={"id": "pay-1", "status": "initiated"})) as create,
        patch("api.routes.wallet.get_provider", return_value=provider),
    ):
        r = client.post("/hospital/wallet/topup", json={"credits": 100})
    assert r.status_code == 200
    body = r.json()
    # 100 credits * ৳20 = ৳2000
    assert body["payment"]["amount"] == 2000
    assert body["payment"]["pay_url"] == "http://pay/x"
    kw = create.await_args.kwargs
    assert kw["kind"] == "credit_topup"
    assert kw["credits"] == 100
    assert kw["hospital_id"] == 10
    assert kw["amount"] == 2000


def test_topup_autopay_confirms_and_loads_wallet(client):
    _as(_HOSPITAL_ADMIN)
    provider = MagicMock()
    provider.initiate = AsyncMock(return_value={"auto_paid": True})
    loaded = {**_WALLET, "balance": 140}
    with (
        patch("api.routes.wallet.get_or_create_hospital_wallet",
              new=AsyncMock(side_effect=[_WALLET, loaded])),
        patch("api.routes.wallet.get_hospital", new=AsyncMock(return_value={"name": "City"})),
        patch("api.routes.wallet.create_payment",
              new=AsyncMock(return_value={"id": "pay-2", "status": "initiated"})),
        patch("api.routes.wallet.get_provider", return_value=provider),
        patch("api.routes.wallet.confirm_paid_booking",
              new=AsyncMock(return_value={"status": "ok", "kind": "credit_topup"})) as confirm,
    ):
        r = client.post("/hospital/wallet/topup", json={"credits": 100})
    assert r.status_code == 200
    body = r.json()
    confirm.assert_awaited_once()
    assert body["payment"]["pay_url"] is None   # already paid
    assert body["balance"] == 140


# --- superadmin wallet controls --------------------------------------------

def test_platform_wallet_requires_platform_admin(client):
    _as(_HOSPITAL_ADMIN)
    assert client.get("/platform/hospitals/10/wallet").status_code == 403


def test_platform_set_rate(client):
    _as(_PLATFORM_ADMIN)
    with (
        patch("api.routes.platform.get_hospital", new=AsyncMock(return_value={"id": 10})),
        patch("api.routes.platform.set_wallet_rate",
              new=AsyncMock(return_value={"credit_rate_bdt": 12.5})) as setr,
    ):
        r = client.post("/platform/hospitals/10/wallet/rate", json={"credit_rate_bdt": 12.5})
    assert r.status_code == 200
    assert r.json()["credit_rate_bdt"] == 12.5
    setr.assert_awaited_once_with(10, 12.5)


def test_platform_grant_credits(client):
    _as(_PLATFORM_ADMIN)
    with (
        patch("api.routes.platform.get_hospital", new=AsyncMock(return_value={"id": 10})),
        patch("api.routes.platform.get_or_create_hospital_wallet", new=AsyncMock(return_value=_WALLET)),
        patch("api.routes.platform.adjust_wallet",
              new=AsyncMock(return_value={"balance_after": 240})) as adj,
    ):
        r = client.post("/platform/hospitals/10/wallet/grant", json={"credits": 200, "note": "comp"})
    assert r.status_code == 200
    assert r.json()["balance_after"] == 240
    assert adj.await_args.kwargs["reason"] == "grant"


def test_platform_grant_clawback_uses_adjustment(client):
    _as(_PLATFORM_ADMIN)
    with (
        patch("api.routes.platform.get_hospital", new=AsyncMock(return_value={"id": 10})),
        patch("api.routes.platform.get_or_create_hospital_wallet", new=AsyncMock(return_value=_WALLET)),
        patch("api.routes.platform.adjust_wallet",
              new=AsyncMock(return_value={"balance_after": -10})) as adj,
    ):
        r = client.post("/platform/hospitals/10/wallet/grant", json={"credits": -50})
    assert r.status_code == 200
    assert adj.await_args.kwargs["reason"] == "adjustment"
    assert adj.await_args.kwargs["delta"] == -50
