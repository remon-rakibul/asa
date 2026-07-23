"""Cross-hospital doctor search (marketplace) + fee CRUD, with mocked asyncpg
connections — same pattern as test_database.py: assert on the SQL text and
bind params the data layer sends."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_pool_conn(fetch_return=None, fetchrow_return=None):
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=fetch_return or [])
    conn.fetchrow = AsyncMock(return_value=fetchrow_return)
    conn.fetchval = AsyncMock(return_value=1)
    conn.execute = AsyncMock(return_value="UPDATE 1")
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
# search_doctors_platform — SQL shape, filters, sorts, pagination
# ---------------------------------------------------------------------------

async def test_search_default_sorts_by_rating():
    from tools.database import search_doctors_platform

    pool, conn = _make_pool_conn()
    with patch("tools.database.get_pool", new=AsyncMock(return_value=pool)):
        await search_doctors_platform(q="cardio")
    sql, *params = conn.fetch.call_args.args
    assert "ILIKE" in sql
    assert "avg_rating DESC, review_count DESC" in sql
    assert "status = 'published'" in sql  # hidden reviews excluded from average
    assert params[0] == "cardio"


async def test_search_fee_sort_puts_nulls_last():
    from tools.database import search_doctors_platform

    pool, conn = _make_pool_conn()
    with patch("tools.database.get_pool", new=AsyncMock(return_value=pool)):
        await search_doctors_platform(sort="fee")
    sql = conn.fetch.call_args.args[0]
    assert "d.fee_new ASC NULLS LAST" in sql


async def test_search_unknown_sort_falls_back_to_rating():
    from tools.database import search_doctors_platform

    pool, conn = _make_pool_conn()
    with patch("tools.database.get_pool", new=AsyncMock(return_value=pool)):
        await search_doctors_platform(sort="'; DROP TABLE doctors; --")
    sql = conn.fetch.call_args.args[0]
    assert "DROP TABLE" not in sql
    assert "avg_rating DESC" in sql


async def test_search_max_fee_excludes_null_fees():
    # `d.fee_new <= $4` is NULL (not TRUE) for doctors without a fee, so an
    # unknown fee can never satisfy a budget filter.
    from tools.database import search_doctors_platform

    pool, conn = _make_pool_conn()
    with patch("tools.database.get_pool", new=AsyncMock(return_value=pool)):
        await search_doctors_platform(max_fee=500)
    sql, *params = conn.fetch.call_args.args
    assert "d.fee_new <= $4" in sql
    assert params[3] == 500


async def test_search_filters_and_pagination_params():
    from tools.database import search_doctors_platform

    pool, conn = _make_pool_conn()
    with patch("tools.database.get_pool", new=AsyncMock(return_value=pool)):
        await search_doctors_platform(
            q=" রহিম ", specialty="Cardiology", hospital_id=3,
            limit=20, offset=40,
        )
    _, q, specialty, hospital_id, max_fee, limit, offset = conn.fetch.call_args.args
    assert q == "রহিম"           # stripped
    assert specialty == "Cardiology"
    assert hospital_id == 3
    assert max_fee is None
    assert (limit, offset) == (20, 40)


async def test_search_matches_across_name_specialty_department_hospital():
    from tools.database import search_doctors_platform

    pool, conn = _make_pool_conn()
    with patch("tools.database.get_pool", new=AsyncMock(return_value=pool)):
        await search_doctors_platform(q="x")
    sql = conn.fetch.call_args.args[0]
    for col in ("d.name ILIKE", "d.specialty ILIKE", "c.name ILIKE", "h.name ILIKE"):
        assert col in sql


async def test_get_doctor_public_missing_returns_none():
    from tools.database import get_doctor_public

    pool, conn = _make_pool_conn(fetchrow_return=None)
    with patch("tools.database.get_pool", new=AsyncMock(return_value=pool)):
        assert await get_doctor_public(999) is None


# ---------------------------------------------------------------------------
# Fee CRUD — nullable-clear semantics in update_doctor
# ---------------------------------------------------------------------------

# Row rich enough for update/add to complete (_resync_primary re-reads it).
_DOCTOR_ROW = {"id": 5, "name": "Rahim", "phone": "", "is_primary": True}


def _call_matching(conn, fragment: str):
    """The first fetchrow call whose SQL contains `fragment`."""
    for call in conn.fetchrow.call_args_list:
        if fragment in call.args[0]:
            return call.args
    raise AssertionError(f"no fetchrow call matching {fragment!r}")


async def test_update_doctor_explicit_null_clears_fee():
    from tools.database import update_doctor

    pool, conn = _make_pool_conn(fetchrow_return=dict(_DOCTOR_ROW))
    with patch("tools.database.get_pool", new=AsyncMock(return_value=pool)):
        await update_doctor(1, 5, fee_new=None, fee_followup=None)
    sql, *params = _call_matching(conn, "UPDATE doctors SET")
    # Fees ARE in the SET clause with NULL values (clear), unlike other fields.
    assert "fee_new=$3" in sql and "fee_followup=$4" in sql
    assert params == [5, 1, None, None]


async def test_update_doctor_drops_none_for_non_nullable_fields():
    from tools.database import update_doctor

    pool, conn = _make_pool_conn(fetchrow_return=dict(_DOCTOR_ROW))
    with patch("tools.database.get_pool", new=AsyncMock(return_value=pool)):
        await update_doctor(1, 5, name=None, fee_new=700)
    sql = _call_matching(conn, "UPDATE doctors SET")[0]
    assert "name=" not in sql          # None name is "leave unchanged"
    assert "fee_new=$3" in sql


async def test_add_doctor_inserts_fees():
    from tools.database import add_doctor

    pool, conn = _make_pool_conn(fetchrow_return=dict(_DOCTOR_ROW))
    with patch("tools.database.get_pool", new=AsyncMock(return_value=pool)):
        await add_doctor(clinic_id=1, name="Rahim", fee_new=800, fee_followup=500)
    args = _call_matching(conn, "INSERT INTO doctors")
    assert "fee_new" in args[0] and "fee_followup" in args[0]
    assert 800 in args and 500 in args
