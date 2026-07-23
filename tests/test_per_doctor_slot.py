"""Regression: slot conflict logic must be scoped PER DOCTOR, not per clinic.

Availability is computed per doctor, and uq_confirmed_slot is now
(clinic_id, COALESCE(doctor_id, 0), scheduled_at) — see migration 0029. The two
Python query sites that reason about slot conflicts must carry the same doctor
dimension, or they'd wrongly reject a free slot on a *different* doctor:

  * book_appointment's UniqueViolation retry-clear (must not cancel another
    doctor's live pending hold), and
  * reschedule_appointment's pre-flight clash check (must ignore other doctors).
"""

from __future__ import annotations

import asyncpg
import pytest

from tests.test_database import _make_pool_conn


@pytest.mark.asyncio
async def test_reschedule_clash_check_is_doctor_scoped(monkeypatch):
    import tools.database as db

    pool, conn = _make_pool_conn()
    # No clash found; the appointment doesn't exist so the UPDATE returns None
    # (we only care about the clash-check SQL, not the full happy path).
    conn.fetchval.return_value = None
    conn.fetchrow.return_value = None
    monkeypatch.setattr(db, "get_pool", lambda: _await(pool))

    await db.reschedule_appointment(1, "00000000-0000-0000-0000-000000000000",
                                    "2099-01-01T10:00:00+00:00")

    clash_sql = conn.fetchval.call_args_list[0].args[0]
    assert "doctor_id IS NOT DISTINCT FROM" in clash_sql, clash_sql


@pytest.mark.asyncio
async def test_book_retry_clear_is_doctor_scoped(monkeypatch):
    import tools.database as db

    pool, conn = _make_pool_conn()
    # First insert loses the unique race; the retry-clear then finds nothing to
    # clear (UPDATE 0) so book_appointment returns None without recursing.
    conn.fetchrow.side_effect = asyncpg.UniqueViolationError("slot taken")
    conn.execute.return_value = "UPDATE 0"
    monkeypatch.setattr(db, "get_pool", lambda: _await(pool))

    result = await db.book_appointment(
        clinic_id=1, patient_name="A", patient_age=30, patient_mobile="0170",
        scheduled_at="2099-01-01T10:00:00+00:00", doctor_id=7,
    )
    assert result is None

    clear_sql = conn.execute.call_args_list[-1].args[0]
    assert "doctor_id IS NOT DISTINCT FROM" in clear_sql, clear_sql


async def _await(value):
    """Tiny awaitable wrapper so get_pool can be monkeypatched with a lambda."""
    return value
