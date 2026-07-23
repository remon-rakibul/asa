"""tools/database.py payment functions + tools/payments.py provider abstraction."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_pool_conn(fetchrow_side_effect=None, fetchval_side_effect=None, execute_return="UPDATE 1"):
    conn = MagicMock()
    conn.fetchrow = AsyncMock(side_effect=fetchrow_side_effect or [])
    conn.fetchval = AsyncMock(side_effect=fetchval_side_effect or [])
    conn.fetch = AsyncMock(return_value=[])
    conn.execute = AsyncMock(return_value=execute_return)
    conn.executemany = AsyncMock()
    pool = MagicMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=cm)
    return pool, conn


# ---------------------------------------------------------------------------
# create_payment / resolve_booking_fee / hospital visibility
# ---------------------------------------------------------------------------

async def test_create_payment_inserts_and_returns_row():
    from tools.database import create_payment

    row = {
        "id": "pay-1", "kind": "booking_fee", "appointment_id": "apt-1",
        "account_id": 7, "hospital_id": 1, "amount": 30, "currency": "BDT",
        "provider": "manual", "provider_ref": "ref-1", "status": "initiated",
    }
    pool, conn = _make_pool_conn(fetchrow_side_effect=[row])
    with patch("tools.database._pool", pool):
        result = await create_payment(
            kind="booking_fee", amount=30, provider="manual", provider_ref="ref-1",
            appointment_id="apt-1", account_id=7, hospital_id=1,
        )
    assert result == row
    sql, *params = conn.fetchrow.call_args[0]
    assert "INSERT INTO payments" in sql
    assert params[0] == "booking_fee" and params[4] == 30


async def test_resolve_booking_fee_telephony_exempt():
    from tools.database import resolve_booking_fee

    assert await resolve_booking_fee(2, None) == 0
    assert await resolve_booking_fee(2, 0) == 0  # falsy account_id


_FREE_ACCOUNT = {"plan": "free", "premium_until": None, "trial_ends_at": None}


async def test_resolve_booking_fee_uses_hospital_fee_when_set():
    from tools.database import resolve_booking_fee

    # First fetchrow = the account (free tier, so no fee exemption).
    pool, conn = _make_pool_conn(fetchrow_side_effect=[_FREE_ACCOUNT], fetchval_side_effect=[50])
    with patch("tools.database._pool", pool):
        fee = await resolve_booking_fee(2, 7)
    assert fee == 50


async def test_resolve_booking_fee_falls_back_to_platform_default():
    from tools.database import resolve_booking_fee

    pool, conn = _make_pool_conn(fetchrow_side_effect=[_FREE_ACCOUNT], fetchval_side_effect=[None])
    with patch("tools.database._pool", pool), patch("tools.database.settings.booking_fee_default", 20):
        fee = await resolve_booking_fee(2, 7)
    assert fee == 20


async def test_resolve_booking_fee_exempts_premium_patient():
    from tools.database import resolve_booking_fee

    premium = {"plan": "premium",
               "premium_until": datetime(2099, 1, 1, tzinfo=timezone.utc),
               "trial_ends_at": None}
    pool, conn = _make_pool_conn(fetchrow_side_effect=[premium])
    with patch("tools.database._pool", pool):
        fee = await resolve_booking_fee(2, 7)
    assert fee == 0


async def test_resolve_booking_fee_exempts_trial_patient():
    from tools.database import resolve_booking_fee

    trial = {"plan": "free", "premium_until": None,
             "trial_ends_at": datetime(2099, 1, 1, tzinfo=timezone.utc)}
    pool, conn = _make_pool_conn(fetchrow_side_effect=[trial])
    with patch("tools.database._pool", pool):
        fee = await resolve_booking_fee(2, 7)
    assert fee == 0


async def test_hospital_bookable_true_when_active_and_not_suspended():
    from tools.database import hospital_bookable

    pool, conn = _make_pool_conn(fetchval_side_effect=[1])
    with patch("tools.database._pool", pool):
        assert await hospital_bookable(1) is True


async def test_hospital_bookable_false_when_query_excludes_it():
    from tools.database import hospital_bookable

    pool, conn = _make_pool_conn(fetchval_side_effect=[None])
    with patch("tools.database._pool", pool):
        assert await hospital_bookable(1) is False


async def test_list_hospitals_public_filters_billing_suspended():
    from tools.database import list_hospitals_public

    pool, conn = _make_pool_conn()
    conn.fetch = AsyncMock(return_value=[])
    with patch("tools.database._pool", pool):
        await list_hospitals_public()
    sql = conn.fetch.call_args[0][0]
    assert "billing_status" in sql and "status = 'active'" in sql


# ---------------------------------------------------------------------------
# confirm_paid_booking — idempotency + resurrection + subscription payments
# ---------------------------------------------------------------------------

def _appt_row(clinic_id=2, doctor_id=5):
    return {
        "clinic_id": clinic_id, "doctor_id": doctor_id,
        "scheduled_at": datetime(2026, 7, 13, 9, 0, tzinfo=timezone.utc),
        "patient_name": "enc(Rahela)", "patient_mobile": "enc(01711000000)",
        "serial_number": 3, "patient_age": 30,
    }


async def test_confirm_paid_booking_happy_path():
    from tools.database import confirm_paid_booking

    pool, conn = _make_pool_conn(
        fetchrow_side_effect=[
            {"appointment_id": "apt-1", "kind": "booking_fee", "account_id": 7},  # UPDATE payments RETURNING
            _appt_row(),                          # _load: appointment row
            {"timezone": "Asia/Dhaka"},            # _load: clinic timezone
        ],
        fetchval_side_effect=["apt-1"],           # UPDATE appointments -> confirmed
    )
    with (
        patch("tools.database._pool", pool),
        patch("tools.database.decrypt_field", side_effect=lambda v: v.replace("enc(", "").rstrip(")")),
        patch("tools.database._record_appointment_event", new=AsyncMock()) as record_event,
    ):
        outcome = await confirm_paid_booking("pay-1", val_id="val-1", raw={"x": 1})

    assert outcome["status"] == "ok"
    assert outcome["appointment_id"] == "apt-1"
    assert outcome["appointment"]["patient_name"] == "Rahela"
    assert outcome["appointment"]["slot_label"]  # a Bangla label was formatted
    record_event.assert_awaited_once()
    assert record_event.await_args.kwargs["event_type"] == "payment_confirmed"


async def test_confirm_paid_booking_double_ipn_is_idempotent():
    """A replayed IPN for an already-paid payment must not error or double-confirm."""
    from tools.database import confirm_paid_booking

    pool, conn = _make_pool_conn(
        fetchrow_side_effect=[
            None,  # UPDATE payments matched nothing (status is already 'paid')
            {"status": "paid", "appointment_id": "apt-1"},  # idempotency check
        ],
    )
    with patch("tools.database._pool", pool):
        outcome = await confirm_paid_booking("pay-1", val_id="val-1", raw={})
    assert outcome == {"status": "already_paid", "appointment_id": "apt-1", "appointment": None}


async def test_confirm_paid_booking_unknown_payment_not_found():
    from tools.database import confirm_paid_booking

    pool, conn = _make_pool_conn(fetchrow_side_effect=[None, None])
    with patch("tools.database._pool", pool):
        outcome = await confirm_paid_booking("pay-x", val_id="v", raw={})
    assert outcome["status"] == "not_found"


async def test_confirm_paid_booking_subscription_activates_premium():
    """A patient-subscription payment isn't linked to any appointment — it
    extends the buyer's premium horizon instead."""
    from tools.database import confirm_paid_booking

    pool, conn = _make_pool_conn(
        fetchrow_side_effect=[
            {"appointment_id": None, "kind": "patient_subscription", "account_id": 7},
        ],
    )
    with patch("tools.database._pool", pool):
        outcome = await confirm_paid_booking("pay-sub", val_id="v", raw={})
    assert outcome["status"] == "ok"
    assert outcome["appointment_id"] is None
    assert outcome["kind"] == "patient_subscription"
    # premium was extended for account 7.
    execs = [c.args[0] for c in conn.execute.call_args_list]
    assert any("patient_accounts" in sql and "premium_until" in sql for sql in execs)


async def test_confirm_paid_booking_generic_no_appointment():
    """A booking-fee payment with no linked appointment (edge case) is a no-op."""
    from tools.database import confirm_paid_booking

    pool, conn = _make_pool_conn(
        fetchrow_side_effect=[
            {"appointment_id": None, "kind": "booking_fee", "account_id": 7},
        ],
    )
    with patch("tools.database._pool", pool):
        outcome = await confirm_paid_booking("pay-x", val_id="v", raw={})
    assert outcome == {"status": "ok", "appointment_id": None, "appointment": None}


async def test_confirm_paid_booking_resurrects_expired_cancelled_hold():
    """Payment lands just after the TTL swept the hold to 'cancelled' — the
    slot is still free, so it's resurrected back to confirmed."""
    from tools.database import confirm_paid_booking

    pool, conn = _make_pool_conn(
        fetchrow_side_effect=[
            {"appointment_id": "apt-2", "kind": "booking_fee", "account_id": 7},
            _appt_row(),
            {"timezone": "UTC"},
        ],
        fetchval_side_effect=[
            None,      # UPDATE ... WHERE status='pending_payment' matched nothing
            "apt-2",   # resurrection UPDATE ... WHERE status='cancelled' succeeded
        ],
    )
    with (
        patch("tools.database._pool", pool),
        patch("tools.database.decrypt_field", side_effect=lambda v: v),
        patch("tools.database._record_appointment_event", new=AsyncMock()) as record_event,
    ):
        outcome = await confirm_paid_booking("pay-2", val_id="v", raw={})
    assert outcome["status"] == "ok"
    assert record_event.await_args.kwargs["event_type"] == "payment_confirmed_after_expiry"


async def test_confirm_paid_booking_refund_needed_when_slot_taken():
    """The slot was taken by someone else before the late payment landed —
    money was collected but the booking can't be honoured; flagged, not lost."""
    from tools.database import confirm_paid_booking

    pool, conn = _make_pool_conn(
        fetchrow_side_effect=[{"appointment_id": "apt-3", "kind": "booking_fee", "account_id": 7}],
        fetchval_side_effect=[None, None],  # neither confirm nor resurrect matched
    )
    with patch("tools.database._pool", pool):
        outcome = await confirm_paid_booking("pay-3", val_id="v", raw={})
    assert outcome["status"] == "resurrect_failed"
    # The refund_needed flag was written onto the payment row.
    assert conn.execute.call_count >= 1
    assert "refund_needed" in conn.execute.call_args[0][0]


# ---------------------------------------------------------------------------
# sweep_expired_payments
# ---------------------------------------------------------------------------

async def test_sweep_expired_payments_cancels_and_marks_expired():
    from tools.database import sweep_expired_payments

    pool, conn = _make_pool_conn()
    conn.fetch = AsyncMock(return_value=[
        {"id": "apt-1", "clinic_id": 2}, {"id": "apt-2", "clinic_id": 3},
    ])
    with (
        patch("tools.database._pool", pool),
        patch("tools.database._record_appointment_event", new=AsyncMock()) as record_event,
    ):
        n = await sweep_expired_payments()
    assert n == 2
    conn.executemany.assert_awaited_once()
    assert record_event.await_count == 2


async def test_sweep_expired_payments_nothing_to_do():
    from tools.database import sweep_expired_payments

    pool, conn = _make_pool_conn()
    conn.fetch = AsyncMock(return_value=[])
    with patch("tools.database._pool", pool):
        n = await sweep_expired_payments()
    assert n == 0
    conn.executemany.assert_not_awaited()


# ---------------------------------------------------------------------------
# tools/payments.py — provider abstraction
# ---------------------------------------------------------------------------

async def test_manual_provider_autopay():
    from tools.payments import ManualProvider

    with patch("tools.payments.settings.payment_manual_autopay", True):
        result = await ManualProvider().initiate(
            payment_id="p1", amount=30, currency="BDT",
            success_url="s", fail_url="f", cancel_url="c", ipn_url="i",
            customer_name="Rahela", customer_phone="01711000000",
        )
    assert result == {"auto_paid": True}


async def test_manual_provider_no_autopay_returns_portal_page():
    from tools.payments import ManualProvider

    with (
        patch("tools.payments.settings.payment_manual_autopay", False),
        patch("tools.payments.settings.portal_base_url", "http://localhost:3000"),
    ):
        result = await ManualProvider().initiate(
            payment_id="p1", amount=30, currency="BDT",
            success_url="s", fail_url="f", cancel_url="c", ipn_url="i",
            customer_name="Rahela", customer_phone="01711000000",
        )
    assert result == {"pay_url": "http://localhost:3000/portal/pay/p1"}


async def test_sslcommerz_initiate_returns_gateway_url():
    from tools.payments import SSLCommerzProvider

    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={
        "status": "SUCCESS", "GatewayPageURL": "https://sandbox.sslcommerz.com/pay/xyz",
    })
    client = AsyncMock()
    client.post = AsyncMock(return_value=resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    with patch("tools.payments.httpx.AsyncClient", return_value=client):
        result = await SSLCommerzProvider().initiate(
            payment_id="p1", amount=30, currency="BDT",
            success_url="s", fail_url="f", cancel_url="c", ipn_url="i",
            customer_name="Rahela", customer_phone="01711000000",
        )
    assert result == {"pay_url": "https://sandbox.sslcommerz.com/pay/xyz"}
    body = client.post.call_args.kwargs["data"]
    assert body["tran_id"] == "p1" and body["total_amount"] == "30.00"


async def test_sslcommerz_initiate_raises_on_failure():
    from tools.payments import SSLCommerzProvider

    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={"status": "FAILED", "failedreason": "bad store id"})
    client = AsyncMock()
    client.post = AsyncMock(return_value=resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    with patch("tools.payments.httpx.AsyncClient", return_value=client):
        with pytest.raises(RuntimeError):
            await SSLCommerzProvider().initiate(
                payment_id="p1", amount=30, currency="BDT",
                success_url="s", fail_url="f", cancel_url="c", ipn_url="i",
                customer_name="Rahela", customer_phone="01711000000",
            )


async def test_sslcommerz_validate_accepts_valid_and_validated():
    from tools.payments import SSLCommerzProvider

    for status in ("VALID", "VALIDATED"):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={"status": status, "tran_id": "p1", "amount": "30.00"})
        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        with patch("tools.payments.httpx.AsyncClient", return_value=client):
            result = await SSLCommerzProvider().validate({"val_id": "v1", "tran_id": "p1"})
        assert result["ok"] is True and result["amount"] == 30.0


async def test_sslcommerz_validate_rejects_invalid_status():
    from tools.payments import SSLCommerzProvider

    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={"status": "INVALID_TRANSACTION"})
    client = AsyncMock()
    client.get = AsyncMock(return_value=resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    with patch("tools.payments.httpx.AsyncClient", return_value=client):
        result = await SSLCommerzProvider().validate({"val_id": "v1", "tran_id": "p1"})
    assert result["ok"] is False


async def test_get_provider_selects_by_setting():
    from tools.payments import get_provider, ManualProvider, SSLCommerzProvider

    with patch("tools.payments.settings.payment_provider", "manual"):
        assert isinstance(get_provider(), ManualProvider)
    with patch("tools.payments.settings.payment_provider", "sslcommerz"):
        assert isinstance(get_provider(), SSLCommerzProvider)
    with patch("tools.payments.settings.payment_provider", "bogus"):
        assert isinstance(get_provider(), ManualProvider)  # unknown -> safe default


# ---------------------------------------------------------------------------
# Hospital subscriptions — free trial on signup + billing sweep + invoicing
# ---------------------------------------------------------------------------

async def test_start_hospital_free_trial_seeds_active_period():
    from tools.database import start_hospital_free_trial

    row = {
        "hospital_id": 5, "monthly_fee": 999, "status": "active",
        "current_period_start": datetime(2026, 7, 12, tzinfo=timezone.utc),
        "current_period_end": datetime(2026, 8, 11, tzinfo=timezone.utc),
    }
    pool, conn = _make_pool_conn(fetchrow_side_effect=[row])
    with patch("tools.database._pool", pool):
        result = await start_hospital_free_trial(5, monthly_fee=999, trial_days=30)
    assert result == row
    sql, *params = conn.fetchrow.call_args[0]
    assert "ON CONFLICT (hospital_id) DO NOTHING" in sql
    # trial_days must be an integer interval via make_interval, NOT a text
    # concat ("$3 || ' days'") — the latter makes asyncpg infer $3 as text and
    # reject the int at bind time on real Postgres (mocks can't catch that).
    assert "make_interval(days => $3)" in sql
    assert "|| ' days'" not in sql
    assert params == [5, 999, 30]


async def test_start_hospital_free_trial_noop_if_already_exists():
    from tools.database import start_hospital_free_trial

    pool, conn = _make_pool_conn(fetchrow_side_effect=[None])
    with patch("tools.database._pool", pool):
        result = await start_hospital_free_trial(5, monthly_fee=999, trial_days=30)
    assert result is None


async def test_sweep_hospital_billing_marks_past_due_and_suspended():
    from tools.database import sweep_hospital_billing

    pool, conn = _make_pool_conn()
    conn.fetch = AsyncMock(return_value=[
        {"hospital_id": 5, "status": "past_due"},
        {"hospital_id": 6, "status": "suspended"},
    ])
    with patch("tools.database._pool", pool):
        n = await sweep_hospital_billing()
    assert n == 2
    conn.executemany.assert_awaited_once()
    calls = conn.executemany.call_args[0][1]
    assert (5, "past_due") in calls and (6, "suspended") in calls


async def test_sweep_hospital_billing_nothing_to_do():
    from tools.database import sweep_hospital_billing

    pool, conn = _make_pool_conn()
    conn.fetch = AsyncMock(return_value=[])
    with patch("tools.database._pool", pool):
        n = await sweep_hospital_billing()
    assert n == 0
    conn.executemany.assert_not_awaited()


async def test_mark_subscription_invoice_paid_advances_period():
    from tools.database import mark_subscription_invoice_paid

    sub_row = {"monthly_fee": 999, "current_period_end": datetime(2026, 8, 11, tzinfo=timezone.utc)}
    updated_sub = {
        "hospital_id": 5, "monthly_fee": 999, "status": "active",
        "current_period_start": datetime(2026, 7, 12, tzinfo=timezone.utc),
        "current_period_end": datetime(2026, 9, 11, tzinfo=timezone.utc),
    }
    invoice_row = {
        "id": 1, "hospital_id": 5, "period_start": datetime(2026, 7, 12).date(),
        "period_end": datetime(2026, 9, 11).date(), "amount": 999,
        "status": "paid", "method": "manual", "paid_at": datetime(2026, 7, 12, tzinfo=timezone.utc),
    }
    pool, conn = _make_pool_conn(
        fetchrow_side_effect=[sub_row, updated_sub, invoice_row],
        fetchval_side_effect=[datetime(2026, 9, 11, tzinfo=timezone.utc)],
    )
    with patch("tools.database._pool", pool):
        result = await mark_subscription_invoice_paid(5, method="manual")
    assert result["subscription"]["status"] == "active"
    assert result["invoice"]["status"] == "paid"


async def test_mark_subscription_invoice_paid_missing_subscription_raises():
    from tools.database import mark_subscription_invoice_paid

    pool, conn = _make_pool_conn(fetchrow_side_effect=[None])
    with patch("tools.database._pool", pool):
        with pytest.raises(ValueError):
            await mark_subscription_invoice_paid(999)


# ---------------------------------------------------------------------------
# _process_ipn amount check: reject UNDERpayment, tolerate the ৳10 gateway floor
# ---------------------------------------------------------------------------

async def _run_ipn(gateway_amount, stored_amount):
    from api.routes import payments as pay

    provider = MagicMock()
    provider.validate = AsyncMock(return_value={
        "ok": True, "provider_ref": "ref-1", "val_id": "v1",
        "amount": gateway_amount, "raw": {},
    })
    payment = {"id": "pmt-1", "amount": stored_amount, "kind": "booking_fee",
               "account_id": 7, "appointment_id": None}
    with (
        patch.object(pay, "get_provider", return_value=provider),
        patch.object(pay, "get_payment_by_provider_ref",
                     new=AsyncMock(return_value=payment)),
        patch.object(pay, "confirm_paid_booking",
                     new=AsyncMock(return_value={"status": "ok", "appointment": None,
                                                 "appointment_id": None})) as confirm,
    ):
        result = await pay._process_ipn({"val_id": "v1"}, graph=MagicMock())
    return result, confirm


async def test_ipn_accepts_gateway_min_charge_over_small_fee():
    # Fee ৳5 stored, but SSLCommerz charges its ৳10 minimum. Must still confirm.
    result, confirm = await _run_ipn(gateway_amount=10.0, stored_amount=5)
    assert result["ok"] is True
    confirm.assert_awaited_once()


async def test_ipn_rejects_underpayment():
    # Patient tampered the checkout to pay ৳20 against a ৳30 fee — reject.
    result, confirm = await _run_ipn(gateway_amount=20.0, stored_amount=30)
    assert result["ok"] is False
    confirm.assert_not_awaited()
