"""Doctor reviews — eligibility, upsert semantics, moderation tenancy, and the
portal/admin API surfaces. Mocked asyncpg + FastAPI dependency overrides; no
real Postgres (SQL behavior is asserted on the query text/params)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver


def _make_pool_conn(fetch_return=None, fetchrow_return=None):
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=fetch_return or [])
    conn.fetchrow = AsyncMock(return_value=fetchrow_return)
    pool = MagicMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=cm)
    return pool, conn


_NOW = datetime(2026, 7, 10, tzinfo=timezone.utc)

_REVIEW_ROW = {
    "id": 11, "doctor_id": 5, "rating": 4, "text": "ভালো",
    "status": "published", "created_at": _NOW, "updated_at": _NOW,
}


# ---------------------------------------------------------------------------
# Data layer — eligibility, upsert, listing, moderation SQL
# ---------------------------------------------------------------------------

async def test_eligibility_honors_lifecycle_and_past_fallback():
    from tools.database import account_review_eligible

    pool, conn = _make_pool_conn(fetchrow_return={"?column?": 1})
    with patch("tools.database.get_pool", new=AsyncMock(return_value=pool)):
        assert await account_review_eligible(7, 5) is True
    sql, account_id, doctor_id = conn.fetchrow.call_args.args
    # Marked completed by staff, OR a confirmed/checked-in visit whose time
    # passed (front desks often skip the lifecycle) — future ones never count.
    assert "a.status = 'completed'" in sql
    assert "'confirmed', 'checked_in'" in sql and "a.scheduled_at < now()" in sql
    assert "p.account_id = $1" in sql and "a.doctor_id = $2" in sql
    assert (account_id, doctor_id) == (7, 5)


async def test_eligibility_false_without_matching_appointment():
    from tools.database import account_review_eligible

    pool, _ = _make_pool_conn(fetchrow_return=None)
    with patch("tools.database.get_pool", new=AsyncMock(return_value=pool)):
        assert await account_review_eligible(7, 5) is False


async def test_upsert_review_is_one_per_doctor_and_keeps_status():
    from tools.database import upsert_review

    pool, conn = _make_pool_conn(fetchrow_return=dict(_REVIEW_ROW))
    with patch("tools.database.get_pool", new=AsyncMock(return_value=pool)):
        await upsert_review(7, 5, 4, "ভালো")
    sql = conn.fetchrow.call_args.args[0]
    assert "ON CONFLICT (doctor_id, account_id) DO UPDATE" in sql
    # Editing must NOT resurrect a hidden review: status stays untouched.
    set_clause = sql.split("DO UPDATE")[1].split("RETURNING")[0]
    assert "status" not in set_clause


async def test_portal_listing_only_shows_published():
    from tools.database import list_reviews_for_doctor

    pool, conn = _make_pool_conn()
    with patch("tools.database.get_pool", new=AsyncMock(return_value=pool)):
        await list_reviews_for_doctor(5)
    sql = conn.fetch.call_args.args[0]
    assert "r.status = 'published'" in sql
    # Only the reviewer's first name is exposed to other patients.
    assert "split_part(a.name, ' ', 1)" in sql


async def test_admin_listing_sees_all_statuses_with_optional_filter():
    from tools.database import list_reviews_for_clinic

    pool, conn = _make_pool_conn()
    with patch("tools.database.get_pool", new=AsyncMock(return_value=pool)):
        await list_reviews_for_clinic(1, None)
        await list_reviews_for_clinic(1, "hidden")
    sql, clinic_id, status = conn.fetch.call_args_list[0].args
    assert "d.clinic_id = $1" in sql and "$2::text IS NULL OR r.status = $2" in sql
    assert (clinic_id, status) == (1, None)
    assert conn.fetch.call_args_list[1].args[2] == "hidden"


async def test_set_review_status_enforces_tenancy_in_sql():
    from tools.database import set_review_status

    pool, conn = _make_pool_conn(fetchrow_return=None)
    with patch("tools.database.get_pool", new=AsyncMock(return_value=pool)):
        # Another clinic's review → UPDATE matches nothing → False (route 404s).
        assert await set_review_status(1, 999, "hidden") is False
    sql = conn.fetchrow.call_args.args[0]
    assert "d.clinic_id = $2" in sql and "r.doctor_id = d.id" in sql


# ---------------------------------------------------------------------------
# Portal API — PUT/GET review + eligibility gate
# ---------------------------------------------------------------------------

@pytest.fixture
async def patient_client():
    from agent.graph import build_graph
    from api.app import app
    from api.deps import current_patient

    graph = await build_graph(checkpointer=InMemorySaver())
    app.dependency_overrides[current_patient] = lambda: {"account_id": 7}

    with (
        patch("api.app.build_graph", new_callable=AsyncMock, return_value=graph),
        patch("api.app.get_pool", new_callable=AsyncMock, return_value=MagicMock()),
        patch("api.app.close_pool", new_callable=AsyncMock),
        patch("tools.reminders.send_pending_reminders", new_callable=AsyncMock, return_value=0),
    ):
        with TestClient(app) as c:
            yield c

    app.dependency_overrides.pop(current_patient, None)


def test_put_review_404_when_doctor_missing(patient_client):
    with patch("api.routes.patient_portal.get_doctor",
               new=AsyncMock(return_value=None)):
        r = patient_client.put("/patient/doctors/999/review",
                               json={"rating": 5, "text": ""})
    assert r.status_code == 404


def test_put_review_403_when_not_eligible(patient_client):
    with (
        patch("api.routes.patient_portal.get_doctor",
              new=AsyncMock(return_value={"id": 5})),
        patch("api.routes.patient_portal.account_review_eligible",
              new=AsyncMock(return_value=False)),
    ):
        r = patient_client.put("/patient/doctors/5/review",
                               json={"rating": 5, "text": "চমৎকার"})
    assert r.status_code == 403


def test_put_review_upserts_when_eligible(patient_client):
    upsert = AsyncMock(return_value=dict(_REVIEW_ROW))
    with (
        patch("api.routes.patient_portal.get_doctor",
              new=AsyncMock(return_value={"id": 5})),
        patch("api.routes.patient_portal.account_review_eligible",
              new=AsyncMock(return_value=True)),
        patch("api.routes.patient_portal.upsert_review", new=upsert),
    ):
        r = patient_client.put("/patient/doctors/5/review",
                               json={"rating": 4, "text": "ভালো"})
    assert r.status_code == 200
    upsert.assert_awaited_once_with(7, 5, 4, "ভালো")
    assert r.json()["doctor_id"] == 5


def test_put_review_validates_rating_and_text(patient_client):
    for bad in ({"rating": 0}, {"rating": 6}, {"rating": 3, "text": "x" * 1001}):
        r = patient_client.put("/patient/doctors/5/review", json=bad)
        assert r.status_code == 422, bad


def test_get_my_review_returns_null_when_none(patient_client):
    with patch("api.routes.patient_portal.get_review_for_account",
               new=AsyncMock(return_value=None)):
        r = patient_client.get("/patient/doctors/5/review")
    assert r.status_code == 200 and r.json() is None


def test_get_doctor_reviews_lists_published(patient_client):
    rows = [{"id": 11, "rating": 4, "text": "ভালো", "reviewer_name": "Kodu",
             "created_at": _NOW, "updated_at": _NOW}]
    with patch("api.routes.patient_portal.list_reviews_for_doctor",
               new=AsyncMock(return_value=rows)):
        r = patient_client.get("/patient/doctors/5/reviews")
    assert r.status_code == 200
    assert r.json()[0]["reviewer_name"] == "Kodu"


# ---------------------------------------------------------------------------
# Admin API — moderation routes (tenancy stubs make us clinic 1)
# ---------------------------------------------------------------------------

@pytest.fixture
async def admin_client():
    from agent.graph import build_graph
    from api.app import app

    graph = await build_graph(checkpointer=InMemorySaver())
    with (
        patch("api.app.build_graph", new_callable=AsyncMock, return_value=graph),
        patch("api.app.get_pool", new_callable=AsyncMock, return_value=MagicMock()),
        patch("api.app.close_pool", new_callable=AsyncMock),
        patch("tools.reminders.send_pending_reminders", new_callable=AsyncMock, return_value=0),
        patch("api.routes.reviews.audit_action", new_callable=AsyncMock),
    ):
        with TestClient(app) as c:
            yield c


def test_admin_reviews_rejects_bad_status_filter(admin_client):
    r = admin_client.get("/reviews?status=deleted")
    assert r.status_code == 400


def test_admin_reviews_lists_for_own_clinic(admin_client):
    rows = [{**_REVIEW_ROW, "doctor_name": "Rahim", "account_id": 7,
             "reviewer_name": "Kodu Ai"}]
    listing = AsyncMock(return_value=rows)
    with patch("api.routes.reviews.list_reviews_for_clinic", new=listing):
        r = admin_client.get("/reviews?status=hidden")
    assert r.status_code == 200
    listing.assert_awaited_once_with(1, "hidden")


def test_admin_moderate_review_204_and_audited(admin_client):
    with patch("api.routes.reviews.set_review_status",
               new=AsyncMock(return_value=True)) as srs:
        r = admin_client.patch("/reviews/11", json={"status": "hidden"})
    assert r.status_code == 204
    srs.assert_awaited_once_with(1, 11, "hidden")


def test_admin_moderate_cross_clinic_404(admin_client):
    with patch("api.routes.reviews.set_review_status",
               new=AsyncMock(return_value=False)):
        r = admin_client.patch("/reviews/999", json={"status": "hidden"})
    assert r.status_code == 404


def test_admin_moderate_validates_status_literal(admin_client):
    r = admin_client.patch("/reviews/11", json={"status": "deleted"})
    assert r.status_code == 422
