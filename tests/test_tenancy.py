"""Tests that the data layer scopes queries by clinic_id (tenant isolation)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch


def _make_pool_conn(fetch_return=None, fetchrow_return=None, execute_return="UPDATE 1"):
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


async def test_list_appointments_scopes_by_clinic():
    pool, conn = _make_pool_conn(fetch_return=[])
    with patch("tools.database._pool", pool):
        from tools.database import list_appointments
        await list_appointments(clinic_id=42)
    sql, *params = conn.fetch.call_args[0]
    assert "clinic_id = $1" in sql
    assert params[0] == 42  # tenant scope is the first bound parameter


async def test_get_available_slots_doctor_falls_back_to_clinic_schedule():
    """Live-found 2026-07-08: a pre-selected doctor with no dedicated schedule
    rows returned ZERO slots even though the clinic-wide (doctor_id IS NULL)
    schedule had availability — the wizard pre-selects the doctor, so every
    single-doctor department with clinic-level hours showed no slots."""
    from datetime import datetime, time, timedelta

    sched = {
        "day_of_week": (datetime.now() + timedelta(days=1)).weekday(),
        "start_time": time(9, 0), "end_time": time(11, 0), "slot_duration": 30,
    }
    pool, conn = _make_pool_conn()
    # 1: doctor-specific schedule (empty) → 2: clinic-wide schedule → 3: booked
    conn.fetch = AsyncMock(side_effect=[[], [sched], []])
    with patch("tools.database._pool", pool):
        from tools.database import get_available_slots
        slots = await get_available_slots(7, days_ahead=2, doctor_id=5)

    assert slots, "clinic-wide schedule must back a doctor with no dedicated rows"
    doctor_sql = conn.fetch.call_args_list[0][0][0]
    assert "doctor_id = $2" in doctor_sql
    clinic_sql = conn.fetch.call_args_list[1][0][0]
    assert "doctor_id IS NULL" in clinic_sql
    booked_sql = conn.fetch.call_args_list[2][0][0]
    assert "doctor_id" not in booked_sql  # clinic-wide bookings subtracted


async def test_get_available_slots_scopes_both_queries_by_clinic():
    pool, conn = _make_pool_conn(fetch_return=[])
    with patch("tools.database._pool", pool):
        from tools.database import get_available_slots
        await get_available_slots(7, days_ahead=1)
    # schedule query is first, booked query second; both must filter clinic_id
    first_sql = conn.fetch.call_args_list[0][0][0]
    first_params = conn.fetch.call_args_list[0][0][1:]
    second_sql = conn.fetch.call_args_list[1][0][0]
    assert "clinic_id = $1" in first_sql and first_params[0] == 7
    assert "clinic_id = $1" in second_sql


async def test_cancel_appointment_scopes_by_clinic():
    # cancel_appointment now uses UPDATE…RETURNING via fetchrow.
    pool, conn = _make_pool_conn(fetchrow_return={"new_status": "cancelled"})
    with patch("tools.database._pool", pool):
        from tools.database import cancel_appointment
        await cancel_appointment(9, "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    sql, *params = conn.fetchrow.call_args[0]
    assert "clinic_id = $2" in sql
    assert params[1] == 9


async def test_book_appointment_inserts_clinic_id():
    import uuid
    fake_id = uuid.uuid4()
    pool, conn = _make_pool_conn(fetchrow_return={"id": fake_id})
    with patch("tools.database._pool", pool):
        from tools.database import book_appointment
        await book_appointment(
            clinic_id=5, patient_name="x", patient_age=30,
            patient_mobile="01700000000", scheduled_at="2099-01-06T09:00:00+00:00",
        )
    sql, *params = conn.fetchrow.call_args[0]
    assert "clinic_id" in sql
    assert params[0] == 5  # clinic_id is the first inserted column
