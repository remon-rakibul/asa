"""Assemble and compile the appointment-setter ReAct graph.

Architecture:
  START → call_model → (tool calls?) → tools → call_model → … → END

The LLM decides each turn whether to call a tool (get_available_slots,
book_appointment) or respond to the patient. Conversation history is the
only memory needed — no separate phase tracking.

The Postgres checkpointer persists the full message thread between HTTP
turns, keyed by thread_id (the session / LiveKit room id).
"""

from __future__ import annotations

from typing import Optional

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.store.postgres.aio import AsyncPostgresStore
from langgraph.types import RetryPolicy
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from config import settings

from .nodes import call_model_node, post_booking_node
from .router import route_after_tools, should_continue
from .state import AppointmentState
from .tools import ALL_TOOLS

_checkpointer: Optional[AsyncPostgresSaver] = None
_store: Optional[AsyncPostgresStore] = None
_pool: Optional[AsyncConnectionPool] = None


async def _get_checkpointer() -> AsyncPostgresSaver:
    """Create (once) a process-lifetime Postgres checkpointer + patient memory store."""
    global _checkpointer, _store, _pool
    if _checkpointer is None:
        _pool = AsyncConnectionPool(
            conninfo=settings.database_url,
            open=False,
            min_size=settings.db_pool_min,
            max_size=settings.db_pool_max,
            kwargs={
                "autocommit": True,
                "row_factory": dict_row,
                "prepare_threshold": 0,
            },
        )
        await _pool.open()
        _checkpointer = AsyncPostgresSaver(_pool)
        await _checkpointer.setup()
        _store = AsyncPostgresStore(_pool, index=_store_index())
        await _store.setup()
    return _checkpointer


def _store_index() -> dict | None:
    """Semantic index for patient memory (local Ollama embeddings, pgvector).

    Only visit summaries are embedded ("fields"); profile items are stored
    without vectors. Returns None (plain key-value store) if the embedding
    model can't be constructed, so memory still works without semantic recall.
    """
    try:
        from langchain_ollama import OllamaEmbeddings

        return {
            "dims": settings.embedding_dims,
            "embed": OllamaEmbeddings(
                model=settings.embedding_model,
                base_url=settings.ollama_base_url,
            ),
            "fields": ["summary"],
        }
    except Exception:
        return None


def _build_uncompiled() -> StateGraph:
    builder = StateGraph(AppointmentState)

    # Retries cover transient failures (Ollama hiccups, DB blips, and — with a
    # cloud LLM — connection/DNS drops: a voice call pegs the CPU and Docker's
    # embedded DNS intermittently fails to resolve openrouter.ai, killing the
    # turn). default_retry_on already retries APIConnectionError; the extra
    # attempts with exponential backoff (0.5s → 1s → 2s) ride out a several-
    # second blip so a booking turn survives it. Each attempt is still capped by
    # the asyncio.wait_for in nodes.py, so a genuinely wedged call can't hang.
    builder.add_node(
        "call_model", call_model_node, retry_policy=RetryPolicy(max_attempts=4)
    )
    builder.add_node("tools", ToolNode(ALL_TOOLS), retry_policy=RetryPolicy(max_attempts=3))
    builder.add_node("post_booking", post_booking_node)

    builder.add_edge(START, "call_model")
    builder.add_conditional_edges("call_model", should_continue, ["tools", END])
    builder.add_conditional_edges("tools", route_after_tools, ["call_model", "post_booking"])
    builder.add_edge("post_booking", END)

    return builder


async def build_graph(checkpointer=None, store=None):
    """Compile the graph.

    Pass a custom checkpointer (e.g. InMemorySaver) for tests;
    defaults to the shared Postgres checkpointer + patient memory store.
    """
    if checkpointer is None:
        checkpointer = await _get_checkpointer()
    if store is None:
        store = _store  # may still be None in tests that pass a custom checkpointer
    return _build_uncompiled().compile(checkpointer=checkpointer, store=store)
