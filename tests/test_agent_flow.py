"""End-to-end agent flow tests using InMemorySaver — no Postgres or Ollama needed.

Mocks are applied to agent.graph.call_model_node BEFORE build_graph is called,
so the compiled graph picks up the fake function. Tool patches target the
module-level names in agent.tools that the @tool functions reference.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage

from agent.runner import run_turn

FAKE_SLOTS = [
    {"datetime": "2099-01-06T09:00:00+00:00", "label": "সোমবার সকাল ৯টা"},
    {"datetime": "2099-01-06T09:30:00+00:00", "label": "সোমবার সকাল সাড়ে ৯টা"},
]
FAKE_APPOINTMENT_ID = str(uuid.uuid4())
FAKE_BOOK_RESULT = {"id": FAKE_APPOINTMENT_ID, "serial_number": 1}


async def _build(fake_model=None):
    """Build graph with InMemorySaver; optionally inject a fake call_model_node."""
    from agent.graph import build_graph
    from langgraph.checkpoint.memory import InMemorySaver
    return await build_graph(checkpointer=InMemorySaver())


# ---------------------------------------------------------------------------
# Greeting
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_greeting_returns_reply():
    """First empty turn returns a spoken greeting."""
    async def fake_model(state):
        return {"messages": [AIMessage(content="আস-সালামু আলাইকুম!")]}

    with patch("agent.graph.call_model_node", new=fake_model):
        graph = await _build()
        result = await run_turn(graph, "s-greet", "")

    assert "আস-সালামু আলাইকুম" in result["reply"]
    assert result["done"] is False


# ---------------------------------------------------------------------------
# Info collection
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_asks_for_missing_field():
    """Agent returns a spoken question when patient info is incomplete."""
    async def fake_model(state):
        return {"messages": [AIMessage(content="আপনার বয়স কত?")]}

    with patch("agent.graph.call_model_node", new=fake_model):
        graph = await _build()
        result = await run_turn(graph, "s-ask-age", "আমার নাম রাহেলা")

    assert result["reply"] == "আপনার বয়স কত?"
    assert result["done"] is False


# ---------------------------------------------------------------------------
# Tool calling — get_available_slots
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_calls_slots_tool_then_presents():
    """Once all info is provided, agent calls get_available_slots and presents them."""
    call_count = {"n": 0}

    async def fake_model(state):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[{
                            "name": "get_available_slots",
                            "args": {},
                            "id": "tc-1",
                            "type": "tool_call",
                        }],
                    )
                ]
            }
        return {"messages": [AIMessage(content="সোমবার সকাল ৯টা সময় আছে।")]}

    with (
        patch("agent.graph.call_model_node", new=fake_model),
        patch("agent.tools._get_available_slots", new=AsyncMock(return_value=FAKE_SLOTS)),
    ):
        graph = await _build()
        result = await run_turn(graph, "s-slots", "রাহেলা ৩৫ 01711000000")

    assert "সোমবার" in result["reply"]
    assert result["done"] is False


# ---------------------------------------------------------------------------
# Full happy path — book_appointment tool
# ---------------------------------------------------------------------------

def _slots_then_book_model(book_args):
    """Fake model that runs the realistic ReAct loop: fetch slots, then book,
    then speak. Booking before fetching slots is blocked by a tool guard, so a
    valid happy path must always fetch first."""
    call_count = {"n": 0}

    async def fake_model(state):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return {"messages": [AIMessage(content="", tool_calls=[{
                "name": "get_available_slots", "args": {},
                "id": "tc-slots", "type": "tool_call"}])]}
        if call_count["n"] == 2:
            return {"messages": [AIMessage(content="", tool_calls=[{
                "name": "book_appointment", "args": book_args,
                "id": "tc-book", "type": "tool_call"}])]}
        return {"messages": [AIMessage(content="আপনার অ্যাপয়েন্টমেন্ট নিশ্চিত হয়েছে। ধন্যবাদ!")]}

    return fake_model


@pytest.mark.asyncio
async def test_full_booking_reaches_done():
    """Simulate the full ReAct loop ending with a confirmed booking."""
    fake_model = _slots_then_book_model({
        "patient_name": "রাহেলা",
        "patient_age": 40,
        "patient_mobile": "01799000000",
        "slot_datetime": FAKE_SLOTS[0]["datetime"],
        "slot_label": FAKE_SLOTS[0]["label"],
    })

    with (
        patch("agent.graph.call_model_node", new=fake_model),
        patch("agent.tools._get_available_slots", new=AsyncMock(return_value=FAKE_SLOTS)),
        patch("agent.tools._book_appointment", new=AsyncMock(return_value=FAKE_BOOK_RESULT)),
        patch("agent.tools.send_booking_confirmation", new=AsyncMock()),
        patch("agent.tools.send_doctor_notification", new=AsyncMock()),
    ):
        graph = await _build()
        result = await run_turn(graph, "s-full-book", "হ্যাঁ, নিশ্চিত করুন")

    assert result["done"] is True
    assert result["appointment_id"] == FAKE_APPOINTMENT_ID
    assert result["patient_name"] == "রাহেলা"
    assert result["phase"] == "farewell"


# ---------------------------------------------------------------------------
# Race condition — booking returns None
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_booking_failure_not_done():
    """If book_appointment returns None (slot raced away), done stays False."""
    fake_model = _slots_then_book_model({
        "patient_name": "করিম",
        "patient_age": 30,
        "patient_mobile": "01700000000",
        "slot_datetime": FAKE_SLOTS[0]["datetime"],
        "slot_label": FAKE_SLOTS[0]["label"],
    })

    with (
        patch("agent.graph.call_model_node", new=fake_model),
        patch("agent.tools._get_available_slots", new=AsyncMock(return_value=FAKE_SLOTS)),
        patch("agent.tools._book_appointment", new=AsyncMock(return_value=None)),
        patch("agent.tools.send_booking_confirmation", new=AsyncMock()),
        patch("agent.tools.send_doctor_notification", new=AsyncMock()),
    ):
        graph = await _build()
        result = await run_turn(graph, "s-race", "হ্যাঁ")

    assert result["done"] is False
    assert result["appointment_id"] is None


# ---------------------------------------------------------------------------
# Booking guards — regression tests for the web-161d39c4 failure (2026-06-22):
# the model fabricated a slot list (never called get_available_slots) and then
# passed the Bangla label as slot_datetime, which was mis-reported as "slot taken".
# ---------------------------------------------------------------------------

async def _run_single_book(book_args, *, book_return=FAKE_BOOK_RESULT,
                           slots_return=FAKE_SLOTS):
    """Run one turn where the model immediately calls book_appointment (no prior
    slot fetch), then speaks. Returns (result, tool_message_content)."""
    seen = {"tool_msg": None}
    call_count = {"n": 0}

    async def fake_model(state):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return {"messages": [AIMessage(content="", tool_calls=[{
                "name": "book_appointment", "args": book_args,
                "id": "tc-book", "type": "tool_call"}])]}
        # Capture what the tool fed back to the model on the second pass.
        from langchain_core.messages import ToolMessage
        for m in reversed(state["messages"]):
            if isinstance(m, ToolMessage):
                seen["tool_msg"] = m.content
                break
        return {"messages": [AIMessage(content="বুঝলাম।")]}

    with (
        patch("agent.graph.call_model_node", new=fake_model),
        patch("agent.tools._get_available_slots", new=AsyncMock(return_value=slots_return)),
        patch("agent.tools._book_appointment", new=AsyncMock(return_value=book_return)),
        patch("agent.tools.send_booking_confirmation", new=AsyncMock()),
        patch("agent.tools.send_doctor_notification", new=AsyncMock()),
    ):
        graph = await _build()
        result = await run_turn(graph, f"s-guard-{uuid.uuid4()}", "সোমবার সকাল দশটা")
    return result, seen["tool_msg"]


@pytest.mark.asyncio
async def test_book_without_fetching_slots_is_rejected():
    """Booking before get_available_slots ran must NOT create an appointment."""
    result, tool_msg = await _run_single_book({
        "patient_name": "সিনস", "patient_age": 18, "patient_mobile": "01688071871",
        "slot_datetime": FAKE_SLOTS[0]["datetime"], "slot_label": FAKE_SLOTS[0]["label"],
    })
    assert tool_msg.startswith("NO_SLOTS_FETCHED")
    assert result["done"] is False
    assert result["appointment_id"] is None


@pytest.mark.asyncio
async def test_label_passed_as_datetime_is_rejected():
    """A Bangla label in slot_datetime must be rejected, not booked or mislabeled
    as 'slot taken'. (Needs slots fetched first to reach the datetime guard.)"""
    call_count = {"n": 0}
    seen = {"tool_msg": None}

    async def fake_model(state):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return {"messages": [AIMessage(content="", tool_calls=[{
                "name": "get_available_slots", "args": {},
                "id": "tc-slots", "type": "tool_call"}])]}
        if call_count["n"] == 2:
            return {"messages": [AIMessage(content="", tool_calls=[{
                "name": "book_appointment",
                "args": {"patient_name": "সিনস", "patient_age": 18,
                         "patient_mobile": "01688071871",
                         "slot_datetime": "সোমবার সকাল দশটা",   # label, not ISO
                         "slot_label": "সোমবার সকাল দশটা"},
                "id": "tc-book", "type": "tool_call"}])]}
        from langchain_core.messages import ToolMessage
        for m in reversed(state["messages"]):
            if isinstance(m, ToolMessage):
                seen["tool_msg"] = m.content
                break
        return {"messages": [AIMessage(content="বুঝলাম।")]}

    with (
        patch("agent.graph.call_model_node", new=fake_model),
        patch("agent.tools._get_available_slots", new=AsyncMock(return_value=FAKE_SLOTS)),
        patch("agent.tools._book_appointment", new=AsyncMock(return_value=FAKE_BOOK_RESULT)),
        patch("agent.tools.send_booking_confirmation", new=AsyncMock()),
        patch("agent.tools.send_doctor_notification", new=AsyncMock()),
    ):
        graph = await _build()
        result = await run_turn(graph, f"s-dt-{uuid.uuid4()}", "সোমবার সকাল দশটা")

    assert seen["tool_msg"].startswith("INVALID_DATETIME")
    assert result["done"] is False
    assert result["appointment_id"] is None


@pytest.mark.asyncio
async def test_invalid_mobile_is_rejected():
    """A malformed mobile must never be booked into a medical record."""
    result, tool_msg = await _run_single_book({
        "patient_name": "করিম", "patient_age": 30, "patient_mobile": "12",
        "slot_datetime": FAKE_SLOTS[0]["datetime"], "slot_label": FAKE_SLOTS[0]["label"],
    })
    # slots_shown was never set, so the slot-fetch guard fires first — booking is
    # still correctly refused and nothing is written.
    assert tool_msg.startswith("NO_SLOTS_FETCHED")
    assert result["appointment_id"] is None
