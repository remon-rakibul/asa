"""agent/tools.py::book_appointment — the platform-fee payment flow.

Fee=0 (the default in most tests) keeps the pre-existing "BOOKED:" behavior
untouched. Fee>0 holds the slot (pending_payment) instead of confirming
immediately: a real gateway returns a pay prompt and the router must NOT
treat this as a completed booking (BOOKED_PENDING_PAYMENT doesn't match the
"BOOKED:" prefix post_booking routes on); the manual provider's autopay
flips straight to confirmed and behaves exactly like a fee-free booking.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_BOOK_ARGS = dict(
    patient_name="রাহেলা", patient_age=40, patient_mobile="01711000000",
    slot_datetime="2026-07-13T09:00:00", slot_label="সোমবার সকাল ৯টা",
    tool_call_id="t1",
)

_STATE = {"clinic_id": 2, "patient_account_id": 7, "patient_id": 42, "slots_shown": True}


@pytest.fixture(autouse=True)
def _stub_usage():
    """These tests exercise the fee flow, not the freemium cap. Treat the
    account as trial (uncapped) and stub the usage counter so the cap pre-check
    and the post-booking increment never touch a real DB."""
    with (
        patch("agent.tools._get_patient_account",
              new=AsyncMock(return_value={"plan": "free", "premium_until": None,
                                          "trial_ends_at": _future()})),
        patch("agent.tools._get_agent_bookings_used", new=AsyncMock(return_value=0)),
        patch("agent.tools._increment_agent_bookings", new=AsyncMock(return_value=1)),
    ):
        yield


def _future():
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone.utc) + timedelta(days=10)


def _provider(**init_result):
    provider = MagicMock()
    provider.initiate = AsyncMock(return_value=init_result)
    return provider


async def test_fee_zero_books_confirmed_unchanged(monkeypatch):
    from agent import tools

    book = AsyncMock(return_value={"id": "apt-1", "serial_number": 1})
    with (
        patch("agent.tools._resolve_booking_fee", new=AsyncMock(return_value=0)),
        patch("agent.tools._book_appointment", new=book),
        patch("agent.tools.send_booking_confirmation", new=AsyncMock()) as sms,
        patch("agent.tools.send_doctor_notification", new=AsyncMock()),
        patch("agent.tools._create_payment", new=AsyncMock()) as create_pay,
    ):
        cmd = await tools.book_appointment.coroutine(**_BOOK_ARGS, state=_STATE)
        await asyncio.sleep(0)  # let the _spawn'd notification task run

    book.assert_awaited_once()
    assert book.await_args.kwargs["status"] == "confirmed"
    assert book.await_args.kwargs["payment_ttl_minutes"] is None
    create_pay.assert_not_awaited()
    assert cmd.update["messages"][0].content == "BOOKED: appointment_id=apt-1, serial_number=1"
    assert cmd.update["appointment_id"] == "apt-1"  # router sees a real BOOKED — post_booking fires
    sms.assert_awaited_once()


async def test_fee_charged_holds_slot_and_returns_pay_prompt(monkeypatch):
    from agent import tools

    book = AsyncMock(return_value={"id": "apt-2", "serial_number": 2})
    payment_row = {"id": "pay-1", "status": "initiated"}
    events = []
    with (
        patch("agent.tools._resolve_booking_fee", new=AsyncMock(return_value=30)),
        patch("agent.tools._book_appointment", new=book),
        patch("agent.tools._get_hospital_id_for_clinic", new=AsyncMock(return_value=1)),
        patch("agent.tools._create_payment", new=AsyncMock(return_value=payment_row)),
        patch("agent.tools._get_payment_provider",
              return_value=_provider(pay_url="http://pay.example/x")),
        patch("agent.tools.get_stream_writer", return_value=lambda ev: events.append(ev)),
        patch("agent.tools.send_booking_confirmation", new=AsyncMock()) as sms,
        patch("agent.tools.send_doctor_notification", new=AsyncMock()) as notify,
    ):
        cmd = await tools.book_appointment.coroutine(**_BOOK_ARGS, state=_STATE)

    assert book.await_args.kwargs["status"] == "pending_payment"
    assert book.await_args.kwargs["payment_ttl_minutes"] == 15

    content = cmd.update["messages"][0].content
    assert content.startswith("BOOKED_PENDING_PAYMENT: appointment_id=apt-2")
    assert "fee=৳30" in content
    # NOT "BOOKED:" — route_after_tools must not fire the deterministic
    # post_booking farewell; the LLM composes the held/pay-to-confirm reply.
    assert not content.startswith("BOOKED:")

    # appointment_id stays unset in state — the booking isn't final yet.
    assert cmd.update["appointment_id"] is None
    assert cmd.update["slot_label"] is None

    # Deterministic UI chrome, not routed through the LLM.
    assert len(events) == 1
    ev = events[0]
    assert {k: ev[k] for k in (
        "type", "appointment_id", "payment_id", "amount", "currency", "pay_url",
    )} == {
        "type": "payment", "appointment_id": "apt-2", "payment_id": "pay-1",
        "amount": 30, "currency": "BDT", "pay_url": "http://pay.example/x",
    }
    # A hold-expiry timestamp rides along so the pay card can show a countdown.
    assert isinstance(ev["expires_at"], str) and ev["expires_at"]

    # No premature confirmation SMS — that happens only once payment lands.
    sms.assert_not_awaited()
    notify.assert_not_awaited()


async def test_fee_charged_manual_autopay_confirms_immediately(monkeypatch):
    from agent import tools

    book = AsyncMock(return_value={"id": "apt-3", "serial_number": 3})
    payment_row = {"id": "pay-2", "status": "initiated"}
    with (
        patch("agent.tools._resolve_booking_fee", new=AsyncMock(return_value=30)),
        patch("agent.tools._book_appointment", new=book),
        patch("agent.tools._get_hospital_id_for_clinic", new=AsyncMock(return_value=1)),
        patch("agent.tools._create_payment", new=AsyncMock(return_value=payment_row)),
        patch("agent.tools._get_payment_provider", return_value=_provider(auto_paid=True)),
        patch("agent.tools._confirm_paid_booking", new=AsyncMock()) as confirm,
        patch("agent.tools.send_booking_confirmation", new=AsyncMock()) as sms,
        patch("agent.tools.send_doctor_notification", new=AsyncMock()) as notify,
    ):
        cmd = await tools.book_appointment.coroutine(**_BOOK_ARGS, state=_STATE)
        await asyncio.sleep(0)  # let the _spawn'd notification task run

    confirm.assert_awaited_once_with("pay-2", val_id="", raw={"auto_paid": True})
    content = cmd.update["messages"][0].content
    assert content == "BOOKED: appointment_id=apt-3, serial_number=3"
    assert cmd.update["appointment_id"] == "apt-3"
    sms.assert_awaited_once()
    notify.assert_awaited_once()


async def test_telephony_caller_never_charged(monkeypatch):
    """No patient_account_id (anonymous telephony) — always fee-exempt,
    resolve_booking_fee isn't even consulted."""
    from agent import tools

    book = AsyncMock(return_value={"id": "apt-4", "serial_number": 4})
    fee_fn = AsyncMock(return_value=0)
    with (
        patch("agent.tools._resolve_booking_fee", new=fee_fn),
        patch("agent.tools._book_appointment", new=book),
        patch("agent.tools.send_booking_confirmation", new=AsyncMock()),
        patch("agent.tools.send_doctor_notification", new=AsyncMock()),
    ):
        cmd = await tools.book_appointment.coroutine(
            **_BOOK_ARGS, state={"clinic_id": 2, "slots_shown": True},
        )
    assert book.await_args.kwargs["status"] == "confirmed"
    assert cmd.update["messages"][0].content.startswith("BOOKED:")


async def test_payment_initiate_failure_falls_back_gracefully(monkeypatch):
    """The gateway call itself blowing up must not crash the booking — the
    slot stays held, no pay_url is offered (init.get returns None)."""
    from agent import tools

    book = AsyncMock(return_value={"id": "apt-5", "serial_number": 5})
    payment_row = {"id": "pay-3", "status": "initiated"}
    provider = MagicMock()
    provider.initiate = AsyncMock(side_effect=RuntimeError("gateway unreachable"))
    with (
        patch("agent.tools._resolve_booking_fee", new=AsyncMock(return_value=30)),
        patch("agent.tools._book_appointment", new=book),
        patch("agent.tools._get_hospital_id_for_clinic", new=AsyncMock(return_value=1)),
        patch("agent.tools._create_payment", new=AsyncMock(return_value=payment_row)),
        patch("agent.tools._get_payment_provider", return_value=provider),
        patch("agent.tools.send_booking_confirmation", new=AsyncMock()),
        patch("agent.tools.send_doctor_notification", new=AsyncMock()),
    ):
        cmd = await tools.book_appointment.coroutine(**_BOOK_ARGS, state=_STATE)
    assert cmd.update["messages"][0].content.startswith("BOOKED_PENDING_PAYMENT:")
    assert cmd.update["appointment_id"] is None
