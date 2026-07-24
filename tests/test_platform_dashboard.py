"""Platform-admin dashboard (/platform/*): role gating, revenue overview,
hospital subscription mark-paid, the payment ledger, manual payment confirm,
and refund flagging."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

NOW = datetime.now(timezone.utc)


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


_ADMIN = {"user_id": 1, "role": "platform_admin"}

_STATS = {
    "booking_fee_revenue": 1500, "patient_sub_revenue": 990, "paid_count": 60,
    "refunds_pending": 1, "subscribers_premium": 10, "subscribers_trialing": 4,
    "open_platform_escalations": 2,
}
_HOSPITALS = [{
    "id": 10, "name": "City", "slug": "city", "billing_status": "active",
    "booking_fee": 30, "subscription_status": "active", "monthly_fee": 999,
    "current_period_end": NOW, "fee_revenue": 1500, "paid_bookings": 50, "dues": 0,
}]


# --- role gating -----------------------------------------------------------

def test_overview_requires_platform_admin(client):
    _as({"user_id": 2, "role": "hospital_admin", "clinic_id": 2, "hospital_id": 10})
    r = client.get("/platform/overview")
    assert r.status_code == 403


def test_overview_returns_revenue_and_hospitals(client):
    _as(_ADMIN)
    with (
        patch("api.routes.platform.platform_revenue_stats", new=AsyncMock(return_value=_STATS)),
        patch("api.routes.platform.list_hospitals_admin", new=AsyncMock(return_value=_HOSPITALS)),
    ):
        r = client.get("/platform/overview")
    assert r.status_code == 200
    body = r.json()
    assert body["booking_fee_revenue"] == 1500
    assert body["patient_sub_revenue"] == 990
    assert body["subscribers_premium"] == 10
    assert len(body["hospitals"]) == 1
    assert body["hospitals"][0]["name"] == "City"


# --- hospital subscription mark-paid ---------------------------------------

def test_mark_hospital_paid_advances_period(client):
    _as(_ADMIN)
    result = {
        "subscription": {"hospital_id": 10, "status": "active"},
        "invoice": {"id": 5, "status": "paid"},
    }
    with patch("api.routes.platform.mark_subscription_invoice_paid",
               new=AsyncMock(return_value=result)) as mock:
        r = client.post("/platform/hospitals/10/subscription/mark-paid")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    mock.assert_awaited_once_with(10)


def test_mark_hospital_paid_404_when_no_subscription(client):
    _as(_ADMIN)
    with patch("api.routes.platform.mark_subscription_invoice_paid",
               new=AsyncMock(side_effect=ValueError("No subscription exists for hospital 99"))):
        r = client.post("/platform/hospitals/99/subscription/mark-paid")
    assert r.status_code == 404


# --- payment ledger + manual confirm + refund ------------------------------

def test_payments_ledger_passes_filters(client):
    _as(_ADMIN)
    with patch("api.routes.platform.list_payments", new=AsyncMock(return_value=[])) as mock:
        r = client.get("/platform/payments?kind=booking_fee&status=paid&hospital_id=10")
    assert r.status_code == 200
    assert mock.call_args.kwargs == {
        "kind": "booking_fee", "status": "paid", "hospital_id": 10, "limit": 100,
    }


def test_mark_payment_paid_confirms_and_notifies(client):
    _as(_ADMIN)
    payment = {"id": "pay-1", "kind": "booking_fee", "account_id": 7}
    outcome = {"status": "ok", "appointment_id": "apt-1",
               "appointment": {"patient_name": "Kodu", "patient_mobile": "017",
                               "slot_label": "সোম", "clinic_id": 2, "doctor_id": 5,
                               "serial_number": 3, "patient_age": 30}}
    with (
        patch("api.routes.platform.get_payment", new=AsyncMock(return_value=payment)),
        patch("api.routes.platform.confirm_paid_booking", new=AsyncMock(return_value=outcome)),
        patch("api.routes.patient_portal._notify_and_remember", new=AsyncMock()) as notify,
    ):
        r = client.post("/platform/payments/pay-1/mark-paid")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    notify.assert_awaited_once()


def test_mark_payment_paid_404_unknown(client):
    _as(_ADMIN)
    with patch("api.routes.platform.get_payment", new=AsyncMock(return_value=None)):
        r = client.post("/platform/payments/nope/mark-paid")
    assert r.status_code == 404


def test_refund_marks_refunded(client):
    _as(_ADMIN)
    with patch("api.routes.platform.refund_payment",
               new=AsyncMock(return_value={"id": "pay-1", "status": "refunded"})) as mock:
        r = client.post("/platform/payments/pay-1/refund?note=duplicate")
    assert r.status_code == 200
    assert mock.await_args.kwargs == {"note": "duplicate"}


def test_refund_409_when_not_paid(client):
    _as(_ADMIN)
    with patch("api.routes.platform.refund_payment", new=AsyncMock(return_value=None)):
        r = client.post("/platform/payments/pay-1/refund")
    assert r.status_code == 409


# --- DB helpers ------------------------------------------------------------

def _pool():
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchrow = AsyncMock()
    conn.fetchval = AsyncMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool, conn


async def test_list_payments_builds_filter_clauses():
    from tools.database import list_payments
    pool, conn = _pool()
    with patch("tools.database._pool", pool):
        await list_payments(kind="booking_fee", status="paid", hospital_id=10, limit=50)
    sql, *params = conn.fetch.call_args[0]
    assert "p.kind = $1" in sql and "p.status = $2" in sql and "p.hospital_id = $3" in sql
    assert params == ["booking_fee", "paid", 10, 50]
    # refund_needed must COALESCE to false — a NULL raw would otherwise yield
    # None and fail the bool response-model at serialization (live 500).
    assert "COALESCE((p.raw->>'refund_needed') = 'true', false)" in sql


async def test_list_payments_no_filters():
    from tools.database import list_payments
    pool, conn = _pool()
    with patch("tools.database._pool", pool):
        await list_payments()
    sql, *params = conn.fetch.call_args[0]
    assert "WHERE" not in sql
    assert params == [100]  # just the limit


async def test_refund_payment_only_touches_paid_rows():
    from tools.database import refund_payment
    pool, conn = _pool()
    conn.fetchrow.return_value = {"id": "pay-1", "status": "refunded"}
    with patch("tools.database._pool", pool):
        row = await refund_payment("pay-1", note="dup")
    assert row["status"] == "refunded"
    sql, *params = conn.fetchrow.call_args[0]
    assert "status = 'paid'" in sql  # never refunds an unpaid/failed payment
    assert params == ["pay-1", "dup"]


async def test_platform_revenue_stats_shape():
    from tools.database import platform_revenue_stats
    pool, conn = _pool()
    # Query order: rev (fetchrow), hosp_sub_rev (fetchval), usage (fetchrow),
    # liability (fetchrow), subs (fetchrow), open_esc (fetchval).
    conn.fetchrow.side_effect = [
        {"booking_fee_revenue": 1500, "patient_sub_revenue": 990,
         "credit_topup_revenue": 500, "paid_count": 60, "refunds_pending": 1},
        {"credits_added": 1000, "credits_booking": 250, "sms_events": 100,
         "voice_minutes": 30, "whatsapp_events": 40},
        {"outstanding_debt": 80, "unused_credits": 600},
        {"premium": 10, "trialing": 4},
    ]
    conn.fetchval.return_value = 2  # hospital_sub_revenue and open_escalations
    with patch("tools.database._pool", pool):
        stats = await platform_revenue_stats()

    # gross = 1500 + 990 + 500 + 2 = 2992
    # channel cost = round(100*0.35 + 30*1.5 + 40*0.6) = 104
    # gateway = round(2992 * 0.02) = 60 ; margin = 2992 - 104 - 60 = 2828
    assert stats["gross_revenue"] == 2992
    assert stats["credit_topup_revenue"] == 500
    assert stats["hospital_sub_revenue"] == 2
    assert stats["estimated_channel_cost"] == 104
    assert stats["gateway_fees"] == 60
    assert stats["net_margin"] == 2828
    assert stats["usage_sms"] == 100
    assert stats["usage_voice_minutes"] == 30
    assert stats["usage_whatsapp"] == 40
    assert stats["credits_sold"] == 1000
    assert stats["outstanding_wallet_debt"] == 80
    assert stats["unused_wallet_credits"] == 600
    assert stats["subscribers_premium"] == 10
    assert stats["subscribers_trialing"] == 4
    assert stats["open_platform_escalations"] == 2
