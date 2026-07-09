"""Unit tests for tools/database.py using mocked asyncpg connections."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_pool_conn(fetch_return=None, fetchrow_return=None, execute_return="UPDATE 1"):
    """Build a mock asyncpg pool + connection."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=fetch_return or [])
    conn.fetchrow = AsyncMock(return_value=fetchrow_return)
    conn.fetchval = AsyncMock(return_value=0)
    conn.execute = AsyncMock(return_value=execute_return)
    # book_appointment opens `async with conn.transaction()` to assign a serial.
    txn = MagicMock()
    txn.__aenter__ = AsyncMock(return_value=conn)
    txn.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=txn)

    pool = MagicMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=cm)
    return pool, conn


# ---------------------------------------------------------------------------
# _format_label
# ---------------------------------------------------------------------------

def test_format_label_morning():
    from tools.database import _format_label
    dt = datetime(2024, 1, 1, 9, 0, tzinfo=timezone.utc)
    label = _format_label(dt)
    assert "সকাল" in label


def test_format_label_afternoon():
    from tools.database import _format_label
    dt = datetime(2024, 1, 1, 14, 0, tzinfo=timezone.utc)
    label = _format_label(dt)
    assert "দুপুর" in label


def test_format_label_evening():
    from tools.database import _format_label
    dt = datetime(2024, 1, 1, 17, 0, tzinfo=timezone.utc)
    label = _format_label(dt)
    assert "বিকেল" in label


def test_format_label_on_the_hour():
    from tools.database import _format_label
    dt = datetime(2024, 1, 1, 9, 0, tzinfo=timezone.utc)
    assert "টা" in _format_label(dt)


# ---------------------------------------------------------------------------
# _parse_time
# ---------------------------------------------------------------------------

def test_parse_time_string():
    from tools.database import _parse_time
    assert _parse_time("09:30") == time(9, 30, 0)


def test_parse_time_with_seconds():
    from tools.database import _parse_time
    assert _parse_time("09:30:00") == time(9, 30, 0)


def test_parse_time_already_time_object():
    from tools.database import _parse_time
    t = time(9, 30)
    assert _parse_time(t) == t


# ---------------------------------------------------------------------------
# book_appointment
# ---------------------------------------------------------------------------

async def test_book_appointment_returns_uuid():
    import asyncpg
    fake_id = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    pool, conn = _make_pool_conn(fetchrow_return={"id": fake_id})

    with patch("tools.database._pool", pool):
        from tools.database import book_appointment
        result = await book_appointment(
            clinic_id=1,
            patient_name="রাহেলা",
            patient_age=42,
            patient_mobile="01711000000",
            scheduled_at="2099-01-06T09:00:00+00:00",
        )
    assert result == {"id": str(fake_id), "serial_number": 1}


async def test_book_appointment_race_returns_none():
    import asyncpg
    pool, conn = _make_pool_conn()
    conn.fetchrow = AsyncMock(side_effect=asyncpg.UniqueViolationError("dup"))

    with patch("tools.database._pool", pool):
        from tools.database import book_appointment
        result = await book_appointment(
            clinic_id=1,
            patient_name="রাহেলা",
            patient_age=42,
            patient_mobile="01711000000",
            scheduled_at="2099-01-06T09:00:00+00:00",
        )
    assert result is None


# ---------------------------------------------------------------------------
# cancel_appointment
# ---------------------------------------------------------------------------

async def test_cancel_appointment_returns_true():
    # cancel_appointment now UPDATE…RETURNING; a returned row means a row changed.
    pool, conn = _make_pool_conn(fetchrow_return={"new_status": "cancelled"})
    with patch("tools.database._pool", pool):
        from tools.database import cancel_appointment
        assert await cancel_appointment(1, "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee") is True


async def test_cancel_appointment_not_found_returns_false():
    pool, conn = _make_pool_conn(fetchrow_return=None)
    with patch("tools.database._pool", pool):
        from tools.database import cancel_appointment
        assert await cancel_appointment(1, "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee") is False


# ---------------------------------------------------------------------------
# set_appointment_status (lifecycle transitions)
# ---------------------------------------------------------------------------

async def test_set_status_unknown_status_is_invalid():
    pool, conn = _make_pool_conn()
    with patch("tools.database._pool", pool):
        from tools.database import set_appointment_status
        res = await set_appointment_status(1, "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "banana")
    assert res["status"] == "invalid"


async def test_set_status_not_found_when_row_missing():
    pool, conn = _make_pool_conn()
    conn.fetchval = AsyncMock(return_value=None)  # current status lookup → no row
    with patch("tools.database._pool", pool):
        from tools.database import set_appointment_status
        res = await set_appointment_status(1, "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "completed")
    assert res["status"] == "not_found"


async def test_set_status_illegal_transition_is_invalid():
    pool, conn = _make_pool_conn()
    conn.fetchval = AsyncMock(return_value="completed")  # terminal → no transitions
    with patch("tools.database._pool", pool):
        from tools.database import set_appointment_status
        res = await set_appointment_status(1, "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "checked_in")
    assert res["status"] == "invalid"
    assert res["from"] == "completed"


async def test_set_status_ok_records_and_returns_appointment():
    pool, conn = _make_pool_conn()
    conn.fetchval = AsyncMock(return_value="confirmed")
    conn.fetchrow = AsyncMock(return_value={
        "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "patient_name": "x", "patient_mobile": "01700000000",
        "scheduled_at": datetime(2099, 1, 6, 9, 0, tzinfo=timezone.utc),
        "status": "checked_in", "serial_number": 1, "doctor_id": None,
    })
    with patch("tools.database._pool", pool):
        from tools.database import set_appointment_status
        res = await set_appointment_status(
            1, "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "checked_in",
            actor_user_id=7, actor_role="receptionist",
        )
    assert res["status"] == "ok"
    assert res["appointment"]["status"] == "checked_in"
    # An appointment_events row is inserted (INSERT executed on the mock conn).
    assert any("appointment_events" in str(c[0][0]) for c in conn.execute.call_args_list)


# ---------------------------------------------------------------------------
# appointment_stats (analytics rates)
# ---------------------------------------------------------------------------

async def test_appointment_stats_computes_rates():
    from datetime import date as _date
    pool, conn = _make_pool_conn()
    # Three fetch() calls: status counts, per-doctor, daily trend.
    conn.fetch = AsyncMock(side_effect=[
        [{"status": "completed", "n": 6}, {"status": "no_show", "n": 2},
         {"status": "cancelled", "n": 2}, {"status": "confirmed", "n": 0}],
        [{"doctor_id": 3, "name": "Dr A", "n": 8}],
        [{"day": _date(2026, 6, 1), "n": 5}, {"day": _date(2026, 6, 2), "n": 5}],
    ])
    with patch("tools.database._pool", pool):
        from tools.database import appointment_stats
        res = await appointment_stats(1, "2026-06-01", "2026-06-30")
    assert res["total"] == 10
    assert res["completed"] == 6 and res["no_show"] == 2
    # Rates are over finished (completed + no_show) = 8 → 75% / 25%.
    assert res["completion_rate"] == 75.0
    assert res["no_show_rate"] == 25.0
    assert res["per_doctor"][0]["name"] == "Dr A"
    assert len(res["daily"]) == 2


# ---------------------------------------------------------------------------
# list_appointments
# ---------------------------------------------------------------------------

async def test_list_appointments_empty():
    pool, conn = _make_pool_conn(fetch_return=[])
    with patch("tools.database._pool", pool):
        from tools.database import list_appointments
        result = await list_appointments(clinic_id=1)
    assert result == []


async def test_list_appointments_returns_rows():
    # list_appointments decrypts patient_name AND patient_mobile from each row,
    # so the mock row must carry both columns.
    pool, conn = _make_pool_conn(fetch_return=[
        {"id": "abc", "patient_name": "করিম", "patient_mobile": "01711000000"}
    ])
    with patch("tools.database._pool", pool):
        from tools.database import list_appointments
        result = await list_appointments(clinic_id=1)
    assert len(result) == 1


# ---------------------------------------------------------------------------
# mark_reminder_sent / get_appointments_needing_reminder
# ---------------------------------------------------------------------------

async def test_mark_reminder_sent_executes_update():
    pool, conn = _make_pool_conn()
    with patch("tools.database._pool", pool):
        from tools.database import mark_reminder_sent
        await mark_reminder_sent("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    conn.execute.assert_awaited_once()
    call_sql = conn.execute.call_args[0][0]
    assert "reminder_sent_at" in call_sql


async def test_get_appointments_needing_reminder_returns_rows():
    fake_row = {
        "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "patient_name": "রাহেলা",
        "patient_mobile": "01711000000",
        "scheduled_at": datetime(2099, 1, 6, 9, 0, tzinfo=timezone.utc),
        "slot_iso": "2099-01-06T09:00:00Z",
    }
    pool, conn = _make_pool_conn(fetch_return=[fake_row])
    with patch("tools.database._pool", pool):
        from tools.database import get_appointments_needing_reminder
        result = await get_appointments_needing_reminder()
    assert len(result) == 1
    assert result[0]["patient_name"] == "রাহেলা"


# ---------------------------------------------------------------------------
# get_doctor
# ---------------------------------------------------------------------------

async def test_get_doctor_scoped_to_clinic_returns_row():
    pool, conn = _make_pool_conn(fetchrow_return={"id": 5, "name": "Rahim", "specialty": None, "phone": None, "is_primary": True})
    with patch("tools.database._pool", pool):
        from tools.database import get_doctor
        result = await get_doctor(5, clinic_id=2)
    assert result["name"] == "Rahim"
    args = conn.fetchrow.call_args[0]
    assert "clinic_id" in args[0]
    assert args[1:] == (5, 2)


async def test_get_doctor_clinic_mismatch_returns_none():
    pool, conn = _make_pool_conn(fetchrow_return=None)
    with patch("tools.database._pool", pool):
        from tools.database import get_doctor
        result = await get_doctor(5, clinic_id=999)
    assert result is None


async def test_get_doctor_unscoped_when_clinic_id_omitted():
    pool, conn = _make_pool_conn(fetchrow_return={"id": 5, "clinic_id": 2, "name": "Rahim", "specialty": None, "phone": None, "is_primary": True})
    with patch("tools.database._pool", pool):
        from tools.database import get_doctor
        result = await get_doctor(5)
    assert result["clinic_id"] == 2
    args = conn.fetchrow.call_args[0]
    assert "AND clinic_id" not in args[0]
    assert args[1:] == (5,)
    assert args[1:] == (5,)


# ---------------------------------------------------------------------------
# reschedule_appointment — race with another confirmed booking of the same slot
# ---------------------------------------------------------------------------

async def test_reschedule_race_returns_slot_taken_not_raise():
    import asyncpg
    from datetime import datetime as _dt, timezone as _tz

    pool, conn = _make_pool_conn()
    # No clash on the pre-check, but the UPDATE itself hits the DB's unique
    # index (uq_confirmed_slot) — another booking/reschedule won the race.
    conn.fetchval = AsyncMock(side_effect=[None, _dt(2099, 1, 1, tzinfo=_tz.utc)])
    conn.fetchrow = AsyncMock(side_effect=asyncpg.UniqueViolationError("dup"))
    with patch("tools.database._pool", pool):
        from tools.database import reschedule_appointment
        result = await reschedule_appointment(
            clinic_id=1,
            appointment_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            new_slot_iso="2099-01-06T09:00:00+00:00",
        )
    assert result == {"status": "slot_taken", "appointment": None}


# ---------------------------------------------------------------------------
# get_reminded_appointment_by_phone — ambiguity when 2+ reminded appointments
# ---------------------------------------------------------------------------

async def test_reminded_appointment_single_match_returns_it():
    row = {
        "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "clinic_id": 1,
        "patient_name": "রাহেলা", "patient_mobile": "01711000000",
        "scheduled_at": datetime(2099, 1, 6, 9, 0, tzinfo=timezone.utc),
        "serial_number": 1, "patient_confirmed_at": None,
    }
    pool, conn = _make_pool_conn(fetch_return=[row])
    with patch("tools.database._pool", pool), \
         patch("tools.database.decrypt_field", side_effect=lambda v: v):
        from tools.database import get_reminded_appointment_by_phone
        result = await get_reminded_appointment_by_phone("01711000000")
    assert result is not None
    assert result["id"] == row["id"]


async def test_reminded_appointment_ambiguous_when_multiple_match():
    def _row(id_):
        return {
            "id": id_, "clinic_id": 1,
            "patient_name": "রাহেলা", "patient_mobile": "01711000000",
            "scheduled_at": datetime(2099, 1, 6, 9, 0, tzinfo=timezone.utc),
            "serial_number": 1, "patient_confirmed_at": None,
        }
    pool, conn = _make_pool_conn(fetch_return=[_row("a"), _row("b")])
    with patch("tools.database._pool", pool), \
         patch("tools.database.decrypt_field", side_effect=lambda v: v):
        from tools.database import get_reminded_appointment_by_phone
        result = await get_reminded_appointment_by_phone("01711000000")
    assert result is None


# ---------------------------------------------------------------------------
# queue.py tenant scoping helper (_clinic_scope)
# ---------------------------------------------------------------------------

def test_clinic_scope_platform_admin_is_unscoped():
    from api.routes.queue import _clinic_scope
    assert _clinic_scope({"role": "platform_admin"}) is None


def test_clinic_scope_returns_own_clinic_id():
    from api.routes.queue import _clinic_scope
    assert _clinic_scope({"role": "receptionist", "clinic_id": 5}) == 5


def test_clinic_scope_raises_when_unscoped_non_platform_admin():
    from fastapi import HTTPException
    from api.routes.queue import _clinic_scope
    with pytest.raises(HTTPException):
        _clinic_scope({"role": "receptionist", "clinic_id": None})
