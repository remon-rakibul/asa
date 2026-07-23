"""Global error-handling contract: a client-supplied value that Postgres can't
cast to the target column type (e.g. a non-UUID appointment id hitting
`id = $1::uuid`) must surface as a 400, not a 500. Regression for the bug where
GET/POST on `/appointments/<non-uuid>` and friends leaked Internal Server Error.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver


@pytest.fixture
async def client():
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
        # raise_server_exceptions=False so a genuine 500 comes back as a
        # response we can assert on, instead of being re-raised into the test.
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
    app.dependency_overrides.pop(current_patient, None)


def _uuid_cast_error():
    return asyncpg.exceptions.InvalidTextRepresentationError(
        'invalid input syntax for type uuid: "99999999"'
    )


def test_malformed_uuid_path_returns_400_not_500(client):
    """A DataError bubbling out of the data layer maps to 400."""
    with patch(
        "api.routes.patient_portal.cancel_appointment_for_account",
        new=AsyncMock(side_effect=_uuid_cast_error()),
    ):
        resp = client.post("/patient/appointments/99999999/cancel")
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == "Invalid value in request"


def test_generic_error_still_500(client):
    """A non-DataError failure keeps returning the opaque 500 — the DataError
    handler must not swallow unrelated bugs."""
    with patch(
        "api.routes.patient_portal.cancel_appointment_for_account",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        resp = client.post("/patient/appointments/00000000-0000-0000-0000-000000000000/cancel")
    assert resp.status_code == 500, resp.text
