"""book_appointment must only book a slot the patient was actually offered.

Regression for the hallucinated-slot bug: the tool told the model to copy an
ISO datetime verbatim from AVAILABLE_SLOTS, but nothing enforced it and the DB
layer inserts whatever scheduled_at it is handed. A smaller local model could
fabricate a plausible-but-unoffered time (a past date, an out-of-hours 03:00
slot, a day the doctor doesn't work) that still parses, creating an impossible
appointment. The tool now rejects any slot_datetime not present in
state["offered_slots"], comparing by instant.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

_OFFERED = [
    {"label": "সোমবার সকাল ৯টা", "datetime": "2026-08-17T09:00:00+06:00"},
    {"label": "সোমবার সকাল ৯টা ৩০", "datetime": "2026-08-17T09:30:00+06:00"},
]

_BASE_STATE = {
    "clinic_id": 2, "patient_account_id": 7, "patient_id": 42,
    "slots_shown": True, "offered_slots": _OFFERED,
}


def _args(slot_datetime: str) -> dict:
    return dict(
        patient_name="রাহেলা", patient_age=40, patient_mobile="01711000000",
        slot_datetime=slot_datetime, slot_label="সোমবার সকাল ৯টা",
        tool_call_id="t1",
    )


@pytest.fixture(autouse=True)
def _stub_deps():
    from datetime import datetime, timedelta, timezone
    with (
        patch("agent.tools._resolve_booking_fee", new=AsyncMock(return_value=0)),
        patch("agent.tools._get_patient_account",
              new=AsyncMock(return_value={"plan": "free", "premium_until": None,
                                          "trial_ends_at": datetime.now(timezone.utc)
                                          + timedelta(days=10)})),
        patch("agent.tools._get_agent_bookings_used", new=AsyncMock(return_value=0)),
        patch("agent.tools._increment_agent_bookings", new=AsyncMock(return_value=1)),
        patch("agent.tools.send_booking_confirmation", new=AsyncMock()),
        patch("agent.tools.send_doctor_notification", new=AsyncMock()),
    ):
        yield


def _content(cmd) -> str:
    return cmd.update["messages"][0].content


async def test_unoffered_past_slot_is_rejected():
    """A parseable-but-never-offered past datetime must not reach the DB."""
    from agent import tools

    book = AsyncMock(return_value={"id": "apt-x", "serial_number": 1})
    with patch("agent.tools._book_appointment", new=book):
        cmd = await tools.book_appointment.coroutine(
            **_args("2020-01-01T09:00:00+06:00"), state=_BASE_STATE
        )
    book.assert_not_awaited()
    assert "SLOT_NOT_OFFERED" in _content(cmd)


async def test_unoffered_out_of_hours_slot_is_rejected():
    from agent import tools

    book = AsyncMock(return_value={"id": "apt-x", "serial_number": 1})
    with patch("agent.tools._book_appointment", new=book):
        cmd = await tools.book_appointment.coroutine(
            **_args("2026-08-17T03:00:00+06:00"), state=_BASE_STATE
        )
    book.assert_not_awaited()
    assert "SLOT_NOT_OFFERED" in _content(cmd)


async def test_offered_slot_books_through():
    """An exact copy from the offered list proceeds to the DB insert."""
    from agent import tools

    book = AsyncMock(return_value={"id": "apt-1", "serial_number": 1})
    with patch("agent.tools._book_appointment", new=book):
        cmd = await tools.book_appointment.coroutine(
            **_args("2026-08-17T09:30:00+06:00"), state=_BASE_STATE
        )
    book.assert_awaited_once()
    assert book.await_args.kwargs["scheduled_at"] == "2026-08-17T09:30:00+06:00"
    assert "BOOKED" in _content(cmd)


async def test_same_instant_different_offset_spelling_matches():
    """+06:00 offered vs +0600 supplied is the same instant — must book."""
    from agent import tools

    book = AsyncMock(return_value={"id": "apt-1", "serial_number": 1})
    with patch("agent.tools._book_appointment", new=book):
        cmd = await tools.book_appointment.coroutine(
            **_args("2026-08-17T09:00:00+0600"), state=_BASE_STATE
        )
    book.assert_awaited_once()
    assert "BOOKED" in _content(cmd)


async def test_reschedule_rejects_unoffered_slot():
    """reschedule_appointment carries the same guard: a fabricated target time
    is rejected before the confirm interrupt / DB write is ever reached."""
    from agent import tools

    reschedule = AsyncMock(return_value={"status": "ok"})
    state = {
        "patient_account_id": 7,
        "my_appointments": [{"id": "a1", "label": "সোমবার সকাল ৯টা"}],
        "slots_shown": True,
        "offered_slots": _OFFERED,
    }
    with patch("agent.tools._reschedule_appointment_for_account", new=reschedule):
        cmd = await tools.reschedule_appointment.coroutine(
            appointment_number=1,
            slot_datetime="2020-01-01T09:00:00+06:00",  # never offered
            slot_label="সোমবার সকাল ৯টা",
            tool_call_id="t1",
            state=state,
        )
    reschedule.assert_not_awaited()
    assert "SLOT_NOT_OFFERED" in _content(cmd)


async def test_no_offered_slots_key_skips_guard():
    """Backward-compat: state without offered_slots (older callers / tests)
    still books, relying on the slots_shown guard alone."""
    from agent import tools

    book = AsyncMock(return_value={"id": "apt-1", "serial_number": 1})
    with patch("agent.tools._book_appointment", new=book):
        cmd = await tools.book_appointment.coroutine(
            **_args("2026-08-17T09:00:00+06:00"),
            state={"clinic_id": 2, "slots_shown": True},
        )
    book.assert_awaited_once()
    assert "BOOKED" in _content(cmd)
