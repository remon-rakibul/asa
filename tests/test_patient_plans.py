"""Patient freemium: tier derivation, the monthly agent-booking cap, the
free-tier history limit, and the subscription checkout / activation."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

NOW = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# patient_tier — pure derivation
# ---------------------------------------------------------------------------

def test_patient_tier_premium_wins():
    from tools.database import patient_tier
    acct = {"plan": "premium", "premium_until": NOW + timedelta(days=5),
            "trial_ends_at": NOW - timedelta(days=1)}
    assert patient_tier(acct) == "premium"


def test_patient_tier_trial_when_no_live_premium():
    from tools.database import patient_tier
    acct = {"plan": "free", "premium_until": None, "trial_ends_at": NOW + timedelta(days=5)}
    assert patient_tier(acct) == "trial"


def test_patient_tier_free_when_all_elapsed():
    from tools.database import patient_tier
    acct = {"plan": "free", "premium_until": NOW - timedelta(days=1),
            "trial_ends_at": NOW - timedelta(days=1)}
    assert patient_tier(acct) == "free"


def test_patient_tier_free_when_fields_missing():
    from tools.database import patient_tier
    assert patient_tier({}) == "free"


# ---------------------------------------------------------------------------
# usage counter
# ---------------------------------------------------------------------------

def _pool():
    conn = MagicMock()
    conn.fetchval = AsyncMock()
    conn.execute = AsyncMock()
    conn.fetchrow = AsyncMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool, conn


async def test_increment_agent_bookings_upserts_and_returns_total():
    from tools.database import increment_agent_bookings
    pool, conn = _pool()
    conn.fetchval.return_value = 2
    with patch("tools.database._pool", pool):
        total = await increment_agent_bookings(7, "2026-07")
    assert total == 2
    sql = conn.fetchval.call_args[0][0]
    assert "ON CONFLICT" in sql and "patient_usage" in sql


async def test_get_agent_bookings_used_defaults_zero():
    from tools.database import get_agent_bookings_used
    pool, conn = _pool()
    conn.fetchval.return_value = None
    with patch("tools.database._pool", pool):
        used = await get_agent_bookings_used(7, "2026-07")
    assert used == 0


async def test_activate_patient_subscription_stacks_and_flips_plan():
    from tools.database import activate_patient_subscription
    pool, conn = _pool()
    conn.fetchrow.return_value = {"id": 7, "plan": "premium",
                                  "premium_until": NOW + timedelta(days=30)}
    with patch("tools.database._pool", pool):
        row = await activate_patient_subscription(7, days=30)
    assert row["plan"] == "premium"
    sql = conn.fetchrow.call_args[0][0]
    assert "GREATEST" in sql and "premium_until" in sql and "plan = 'premium'" in sql


# ---------------------------------------------------------------------------
# agent book_appointment — free-tier monthly cap
# ---------------------------------------------------------------------------

_BOOK_ARGS = dict(
    patient_name="রাহেলা", patient_age=40, patient_mobile="01711000000",
    slot_datetime="2026-07-13T09:00:00", slot_label="সোমবার সকাল ৯টা",
    tool_call_id="t1",
)
_STATE = {"clinic_id": 2, "patient_account_id": 7, "patient_id": 42, "slots_shown": True}


async def test_agent_booking_blocked_when_free_cap_reached():
    from agent import tools
    events = []
    free = {"plan": "free", "premium_until": None, "trial_ends_at": None}
    book = AsyncMock()
    with (
        patch("agent.tools._get_patient_account", new=AsyncMock(return_value=free)),
        patch("agent.tools._get_agent_bookings_used", new=AsyncMock(return_value=3)),
        patch("agent.tools.settings.free_agent_bookings_per_month", 3),
        patch("agent.tools._book_appointment", new=book),
        patch("agent.tools.get_stream_writer", return_value=lambda ev: events.append(ev)),
    ):
        cmd = await tools.book_appointment.coroutine(**_BOOK_ARGS, state=_STATE)

    # No booking attempted; the tool returns the limit sentinel + an upgrade event.
    book.assert_not_awaited()
    content = cmd.update["messages"][0].content
    assert content.startswith("BOOKING_LIMIT_REACHED")
    assert events == [{"type": "upgrade", "feature": "chat_bookings", "used": 3, "cap": 3}]


async def test_agent_booking_allowed_and_counted_under_cap():
    from agent import tools
    free = {"plan": "free", "premium_until": None, "trial_ends_at": None}
    book = AsyncMock(return_value={"id": "apt-1", "serial_number": 1})
    inc = AsyncMock(return_value=1)
    with (
        patch("agent.tools._get_patient_account", new=AsyncMock(return_value=free)),
        patch("agent.tools._get_agent_bookings_used", new=AsyncMock(return_value=0)),
        patch("agent.tools.settings.free_agent_bookings_per_month", 3),
        patch("agent.tools._resolve_booking_fee", new=AsyncMock(return_value=0)),
        patch("agent.tools._book_appointment", new=book),
        patch("agent.tools._increment_agent_bookings", new=inc),
        patch("agent.tools.send_booking_confirmation", new=AsyncMock()),
        patch("agent.tools.send_doctor_notification", new=AsyncMock()),
    ):
        cmd = await tools.book_appointment.coroutine(**_BOOK_ARGS, state=_STATE)
        await asyncio.sleep(0)

    book.assert_awaited_once()
    inc.assert_awaited_once_with(7)
    assert cmd.update["messages"][0].content.startswith("BOOKED:")


async def test_agent_booking_premium_not_capped(monkeypatch):
    from agent import tools
    premium = {"plan": "premium", "premium_until": NOW + timedelta(days=5), "trial_ends_at": None}
    used = AsyncMock(return_value=99)  # would be over any cap, but premium is exempt
    book = AsyncMock(return_value={"id": "apt-1", "serial_number": 1})
    with (
        patch("agent.tools._get_patient_account", new=AsyncMock(return_value=premium)),
        patch("agent.tools._get_agent_bookings_used", new=used),
        patch("agent.tools._resolve_booking_fee", new=AsyncMock(return_value=0)),
        patch("agent.tools._book_appointment", new=book),
        patch("agent.tools._increment_agent_bookings", new=AsyncMock(return_value=100)),
        patch("agent.tools.send_booking_confirmation", new=AsyncMock()),
        patch("agent.tools.send_doctor_notification", new=AsyncMock()),
    ):
        cmd = await tools.book_appointment.coroutine(**_BOOK_ARGS, state=_STATE)
        await asyncio.sleep(0)

    used.assert_not_awaited()  # premium short-circuits before counting usage
    book.assert_awaited_once()
    assert cmd.update["messages"][0].content.startswith("BOOKED:")
