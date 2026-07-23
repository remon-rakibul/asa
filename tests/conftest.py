"""Shared pytest fixtures for the appointment-setter test suite.

No Postgres or Ollama is required: the graph uses InMemorySaver and all LLM /
database calls are replaced by mocks.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Graph fixture — uses InMemorySaver so no Postgres checkpointer is needed.
# ---------------------------------------------------------------------------

@pytest.fixture
async def graph():
    """Compiled LangGraph graph backed by an in-memory checkpointer."""
    from agent.graph import build_graph
    return await build_graph(checkpointer=InMemorySaver())


# ---------------------------------------------------------------------------
# LLM mock — all _llm() calls return a fixed Bangla AIMessage.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Reset the process-global rate limiter around every test.

    It's a singleton with a 60-second window, and the whole suite runs in well
    under a minute, so hits from one test's login/patient requests would
    otherwise leak into another's counters (e.g. a signup test getting a
    spurious 429). Nulling it gives each test a clean limit budget.
    """
    import api.ratelimit as rl
    rl._limiter = None
    yield
    rl._limiter = None


@pytest.fixture(autouse=True)
def _reset_db_pool():
    """Reset the module-global asyncpg pool around every test.

    The pool is a lazily-created singleton bound to the event loop that first
    built it. pytest-asyncio gives each test a fresh loop, so a pool leaked from
    a prior test would be bound to a now-closed loop and raise "Event loop is
    closed" the next time any code calls get_pool(). Nulling it keeps tests
    isolated.
    """
    import tools.database as db
    db._pool = None
    yield
    db._pool = None


@pytest.fixture(autouse=True)
def tenancy_stubs():
    """Make multi-tenancy transparent in tests: bypass admin auth (clinic 1) and
    stub channel→clinic resolution + clinic branding so no real DB is hit."""
    from api.app import app
    from api.deps import current_clinic_id, current_user

    fake_clinic = {"name": "Test Clinic", "doctor_name": "Dr. Test", "doctor_phone": ""}
    fake_user = {
        "user_id": 1, "sub": "1", "role": "hospital_admin",
        "clinic_id": 1, "hospital_id": None,
    }
    app.dependency_overrides[current_clinic_id] = lambda: 1
    app.dependency_overrides[current_user] = lambda: fake_user

    with (
        patch("api.routes.chat.resolve_channel_clinic", new=AsyncMock(return_value=1)),
        patch("api.routes.twilio_sms.resolve_channel_clinic", new=AsyncMock(return_value=1)),
        patch("agent.nodes.get_clinic", new=AsyncMock(return_value=fake_clinic)),
        # Audit writes are best-effort side effects; keep API tests hermetic.
        patch("tools.audit.record_audit", new=AsyncMock()),
        patch("api.routes.appointments.record_audit", new=AsyncMock()),
        patch("api.routes.schedule.record_audit", new=AsyncMock()),
        patch("api.routes.auth.record_audit", new=AsyncMock()),
    ):
        yield

    app.dependency_overrides.clear()


@pytest.fixture
def mock_llm():
    """Patch agent.nodes._llm to return a deterministic AIMessage."""
    fake_llm = MagicMock()
    fake_llm.ainvoke = AsyncMock(return_value=AIMessage(content="ঠিক আছে।"))
    fake_llm.with_config = MagicMock(return_value=fake_llm)

    with patch("agent.nodes._llm", return_value=fake_llm):
        yield fake_llm


# ---------------------------------------------------------------------------
# FastAPI TestClient fixture.
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client(graph):
    """Synchronous HTTPX test client wired to the FastAPI app."""
    from httpx import ASGITransport, Client
    from api.app import app

    app.state.graph = graph

    with Client(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
