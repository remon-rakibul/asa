"""Tests for /chat API endpoints using FastAPI TestClient."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver


def _make_model_node(reply="ঠিক আছে।"):
    """Return an async mock that behaves like call_model_node."""
    async def _node(state):
        return {"messages": [AIMessage(content=reply)]}
    return _node


@pytest.fixture
async def client():
    """Async fixture: build graph with InMemorySaver, mock lifespan deps."""
    from api.app import app

    with patch("agent.graph.call_model_node", side_effect=_make_model_node()):
        from agent.graph import build_graph
        graph = await build_graph(checkpointer=InMemorySaver())

    with (
        patch("api.app.build_graph", new_callable=AsyncMock, return_value=graph),
        patch("api.app.get_pool", new_callable=AsyncMock, return_value=MagicMock()),
        patch("api.app.close_pool", new_callable=AsyncMock),
        patch("tools.reminders.send_pending_reminders", new_callable=AsyncMock, return_value=0),
    ):
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] in ("ok", "degraded")


def test_chat_start_returns_greeting(client):
    with patch("agent.nodes.call_model_node", side_effect=_make_model_node("আস-সালামু আলাইকুম! আপনার নাম কী?")):
        r = client.post("/chat/start", json={"session_id": "t-start-1", "message": ""})
    assert r.status_code == 200
    data = r.json()
    assert data["reply"]
    assert data["done"] is False


def test_chat_post_returns_reply(client):
    with patch("agent.nodes.call_model_node", side_effect=_make_model_node("আপনার বয়স কত?")):
        client.post("/chat/start", json={"session_id": "t-chat-1", "message": ""})
        r = client.post("/chat", json={"session_id": "t-chat-1", "message": "রাহেলা"})
    assert r.status_code == 200
    assert r.json()["reply"]


def test_chat_stream_returns_sse(client):
    with patch("agent.nodes.call_model_node", side_effect=_make_model_node("আস-সালামু আলাইকুম!")):
        client.post("/chat/start", json={"session_id": "t-stream-1", "message": ""})
        r = client.post("/chat/stream", json={"session_id": "t-stream-1", "message": "হ্যালো"})

    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
    assert "data:" in r.text
    lines = [l for l in r.text.splitlines() if l.startswith("data:")]
    events = [json.loads(l[6:]) for l in lines]
    assert any(e.get("type") == "end" for e in events)


def test_chat_stream_end_event_has_done_field(client):
    with patch("agent.nodes.call_model_node", side_effect=_make_model_node("ঠিক আছে।")):
        client.post("/chat/start", json={"session_id": "t-stream-2", "message": ""})
        r = client.post("/chat/stream", json={"session_id": "t-stream-2", "message": "হ্যালো"})

    lines = [l for l in r.text.splitlines() if l.startswith("data:")]
    events = [json.loads(l[6:]) for l in lines]
    end = next(e for e in events if e.get("type") == "end")
    assert "done" in end
