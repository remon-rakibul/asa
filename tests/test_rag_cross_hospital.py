"""tools/rag.py::search_docs — cross-hospital search (hospital_id=None) for
the platform-wide assistant, restored alongside per-hospital search."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_pool_conn(fetch_return=None):
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=fetch_return or [])
    pool = MagicMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=cm)
    return pool, conn


@pytest.fixture
def mock_embed(monkeypatch):
    import tools.rag as rag
    embeddings = MagicMock()
    embeddings.aembed_query = AsyncMock(return_value=[0.1, 0.2])
    monkeypatch.setattr(rag, "_embeddings", lambda: embeddings)
    monkeypatch.setattr(rag.settings, "rag_backend", "pgvector")


async def test_search_docs_scoped_to_one_hospital(mock_embed):
    import tools.rag as rag

    pool, conn = _make_pool_conn(fetch_return=[
        {"content": "ভিজিটিং আওয়ার সকাল ৯টা", "filename": "policy.pdf", "hospital_name": "City Hospital"},
    ])
    with patch("tools.database.get_pool", new=AsyncMock(return_value=pool)):
        chunks = await rag.search_docs(5, "ভিজিটিং আওয়ার")

    sql, hospital_id, vec, k = conn.fetch.call_args[0]
    assert hospital_id == 5
    assert "hospital_id = $1" not in sql or "$1::int IS NULL OR" in sql
    # Single-hospital search: no hospital-name prefix, just the filename source.
    assert chunks == ["[উৎস: policy.pdf] ভিজিটিং আওয়ার সকাল ৯টা"]


async def test_search_docs_cross_hospital_when_none(mock_embed):
    import tools.rag as rag

    pool, conn = _make_pool_conn(fetch_return=[
        {"content": "ভিজিটিং আওয়ার সকাল ৯টা", "filename": "policy.pdf", "hospital_name": "City Hospital"},
        {"content": "সন্ধ্যা ৬টা থেকে", "filename": "policy2.pdf", "hospital_name": "Metro Hospital"},
    ])
    with patch("tools.database.get_pool", new=AsyncMock(return_value=pool)):
        chunks = await rag.search_docs(None, "ভিজিটিং আওয়ার")

    sql, hospital_id, vec, k = conn.fetch.call_args[0]
    assert hospital_id is None
    assert "$1::int IS NULL OR" in sql  # the None-safe WHERE clause
    # Cross-hospital search: each chunk names its hospital.
    assert chunks[0] == "[City Hospital — উৎস: policy.pdf] ভিজিটিং আওয়ার সকাল ৯টা"
    assert chunks[1] == "[Metro Hospital — উৎস: policy2.pdf] সন্ধ্যা ৬টা থেকে"


async def test_search_docs_no_hits_returns_empty(mock_embed):
    import tools.rag as rag

    pool, conn = _make_pool_conn(fetch_return=[])
    with patch("tools.database.get_pool", new=AsyncMock(return_value=pool)):
        chunks = await rag.search_docs(None, "কিছু নেই")
    assert chunks == []


async def test_search_docs_db_failure_degrades_to_empty(mock_embed):
    import tools.rag as rag

    pool, conn = _make_pool_conn()
    conn.fetch = AsyncMock(side_effect=RuntimeError("connection reset"))
    with patch("tools.database.get_pool", new=AsyncMock(return_value=pool)):
        chunks = await rag.search_docs(None, "যেকোনো প্রশ্ন")
    assert chunks == []


async def test_search_docs_none_on_chroma_backend_logs_and_returns_empty(monkeypatch):
    import tools.rag as rag

    monkeypatch.setattr(rag.settings, "rag_backend", "chroma")
    chunks = await rag.search_docs(None, "কিছু")
    assert chunks == []
