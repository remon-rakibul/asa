"""Unit tests for token-level streaming in agent.runner.stream_turn_tokens.

Fakes the compiled graph's astream output so the filtering/sanitisation logic
is exercised without a running Ollama or Postgres.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.runner import stream_turn_tokens


class _Chunk:
    """Minimal stand-in for an AIMessageChunk (streaming token delta)."""
    def __init__(self, content: str = "", tool_call_chunks=None):
        self.content = content
        self.tool_call_chunks = tool_call_chunks or []


class _FakeGraph:
    """Minimal stand-in for a compiled LangGraph graph.

    `script` entries are (content, metadata) LLM token deltas replayed as
    "messages"-mode items; an optional third element carries tool_call_chunks
    (a chunk announcing a tool call). `updates` (optional) are (node, delta) pairs
    replayed as "updates"-mode items (for static, non-LLM node output).
    `custom` (optional) are dicts replayed as "custom"-mode items (tool-emitted
    UI events such as {"type": "slots", ...}).
    """

    def __init__(self, script, final_values, updates=None, custom=None, interrupts=()):
        self._script = script
        self._final_values = final_values
        self._updates = updates or []
        self._custom = custom or []
        self._interrupts = tuple(interrupts)
        self.last_input = None  # captured for resume-plumbing assertions

    def astream(self, _input, *, config, stream_mode):
        assert stream_mode == ["messages", "updates", "custom"]
        self.last_input = _input

        async def gen():
            for content, metadata, *rest in self._script:
                tcc = rest[0] if rest else None
                yield "messages", (_Chunk(content, tool_call_chunks=tcc), metadata)
            for payload in self._custom:
                yield "custom", payload
            for node, delta in self._updates:
                yield "updates", {node: delta}

        return gen()

    async def aget_state(self, _config):
        return SimpleNamespace(values=self._final_values, interrupts=self._interrupts)


async def _collect(graph):
    events = []
    async for ev in stream_turn_tokens(graph, "sess-1", "hi"):
        events.append(ev)
    return events


@pytest.mark.asyncio
async def test_tool_node_messages_are_filtered_out():
    """ToolMessages (from "tools" node) must never reach the voice/UI stream."""
    script = [
        # tool result — should be silent
        ('{"slots": []}', {"langgraph_node": "tools"}),
        # spoken reply
        ("উপলব্ধ সময় নেই।", {"langgraph_node": "call_model"}),
    ]
    events = await _collect(_FakeGraph(script, {}))
    tokens = [e["text"] for e in events if e["type"] == "token"]
    assert "".join(tokens) == "উপলব্ধ সময় নেই।"
    assert not any("{" in t for t in tokens)


@pytest.mark.asyncio
async def test_tool_call_generation_chunks_are_filtered_out():
    """Tool-call generation chunks (empty content) must not be emitted."""
    script = [
        # tool-call chunk — empty content
        ("", {"langgraph_node": "call_model"}),
        # spoken reply after tool returns
        ("আপনার নাম কী?", {"langgraph_node": "call_model"}),
    ]
    events = await _collect(_FakeGraph(script, {}))
    tokens = [e["text"] for e in events if e["type"] == "token"]
    assert "".join(tokens) == "আপনার নাম কী?"


@pytest.mark.asyncio
async def test_end_event_carries_state():
    script = [("ধন্যবাদ।", {"langgraph_node": "call_model"})]
    final = {"appointment_id": "abc-123", "patient_name": "রিনা"}
    events = await _collect(_FakeGraph(script, final))

    end = events[-1]
    assert end["type"] == "end"
    assert end["appointment_id"] == "abc-123"
    assert end["patient_name"] == "রিনা"
    assert end["done"] is True
    assert end["phase"] == "farewell"


@pytest.mark.asyncio
async def test_end_event_not_done_when_no_appointment():
    script = [("আপনার মোবাইল নম্বর?", {"langgraph_node": "call_model"})]
    events = await _collect(_FakeGraph(script, {}))
    end = events[-1]
    assert end["done"] is False
    assert end["appointment_id"] is None


@pytest.mark.asyncio
async def test_markdown_spanning_chunks_is_stripped():
    script = [
        ("**", {"langgraph_node": "call_model"}),
        ("আস-সালামু আলাইকুম", {"langgraph_node": "call_model"}),
        ("**", {"langgraph_node": "call_model"}),
    ]
    events = await _collect(_FakeGraph(script, {}))
    tokens = "".join(e["text"] for e in events if e["type"] == "token")
    assert tokens == "আস-সালামু আলাইকুম"


@pytest.mark.asyncio
async def test_static_spoken_response_streams_from_updates():
    """An AIMessage from "updates" (not via token stream) must still be emitted."""
    reply = "আস-সালামু আলাইকুম, আপনার নাম বলুন।"
    graph = _FakeGraph(
        script=[],
        final_values={},
        # Real AIMessage so isinstance check in _spoken_texts passes
        updates=[("call_model", {"messages": [AIMessage(content=reply)]})],
    )
    events = await _collect(graph)
    tokens = "".join(e["text"] for e in events if e["type"] == "token")
    assert tokens == reply


@pytest.mark.asyncio
async def test_list_grouped_superstep_updates_are_spoken():
    """LangGraph groups MULTIPLE updates for one node in a single superstep as a
    LIST of state dicts. _spoken_texts must handle that shape — a bare dict was
    assumed before, so a list crashed the whole turn with
    'list' object has no attribute 'get'."""
    graph = _FakeGraph(
        script=[],
        final_values={},
        # node_delta is a LIST of update dicts (multi-update superstep), not one dict.
        updates=[("call_model", [
            {"messages": [AIMessage(content="প্রথম অংশ। ")]},
            {"messages": [AIMessage(content="দ্বিতীয় অংশ।")]},
        ])],
    )
    events = await _collect(graph)
    tokens = "".join(e["text"] for e in events if e["type"] == "token")
    assert tokens == "প্রথম অংশ। দ্বিতীয় অংশ।"
    # And the turn completed normally (reached the end event, no crash/recovery).
    assert events[-1]["type"] == "end"


@pytest.mark.asyncio
async def test_streamed_node_not_double_emitted_from_updates():
    """When a node streams tokens AND appears in updates, no duplication."""
    graph = _FakeGraph(
        script=[("ধন্যবাদ।", {"langgraph_node": "call_model"})],
        final_values={},
        updates=[("call_model", {"messages": [AIMessage(content="ধন্যবাদ।")]})],
    )
    events = await _collect(graph)
    tokens = "".join(e["text"] for e in events if e["type"] == "token")
    assert tokens == "ধন্যবাদ।"


@pytest.mark.asyncio
async def test_custom_slots_event_is_forwarded_verbatim():
    """A tool-emitted custom event (slots) must reach the UI stream unchanged."""
    slots = [{"label": "সোমবার সকাল ৯টা", "datetime": "2026-06-29T09:00:00+06:00"}]
    graph = _FakeGraph(
        script=[("কোন সময়টি নেবেন?", {"langgraph_node": "call_model"})],
        final_values={},
        custom=[{"type": "slots", "slots": slots}],
    )
    events = await _collect(graph)
    slot_events = [e for e in events if e["type"] == "slots"]
    assert len(slot_events) == 1
    assert slot_events[0]["slots"] == slots


@pytest.mark.asyncio
async def test_suggestions_are_llm_composed_and_follow_end(monkeypatch):
    """Chips come from compose_suggestions (LLM over the thread state) and are
    emitted AFTER the end event so the UI unlocks before the extra LLM call."""
    import agent.nodes as nodes

    async def fake_compose(values, store=None):
        assert values.get("appointment_id") == "abc-123"  # thread state passed
        return ["আমার অ্যাপয়েন্টমেন্ট দেখুন", "নতুন বুকিং"]

    monkeypatch.setattr(nodes, "compose_suggestions", fake_compose)
    script = [("আপনার সিরিয়াল নম্বর পাঁচ।", {"langgraph_node": "call_model"})]
    final = {"appointment_id": "abc-123", "serial_number": 5}
    events = []
    async for ev in stream_turn_tokens(_FakeGraph(script, final), "sess-1", "hi",
                                       suggest=True):
        events.append(ev)
    types = [e["type"] for e in events]
    assert "suggestions" in types
    sugg = next(e for e in events if e["type"] == "suggestions")
    assert sugg["items"] == ["আমার অ্যাপয়েন্টমেন্ট দেখুন", "নতুন বুকিং"]
    assert types.index("suggestions") > types.index("end")


@pytest.mark.asyncio
async def test_suggestions_not_generated_unless_opted_in():
    """Voice/SMS/WhatsApp callers never pay for chip composition (suggest
    defaults to False)."""
    script = [("ধন্যবাদ।", {"langgraph_node": "call_model"})]
    events = await _collect(_FakeGraph(script, {"appointment_id": "x"}))
    assert "suggestions" not in [e["type"] for e in events]


@pytest.mark.asyncio
async def test_interrupt_yields_confirm_event_and_skips_suggestions(monkeypatch):
    """A tool interrupt() surfaces as a confirm event; no chips, end not done."""
    import agent.nodes as nodes

    async def fail_compose(values, store=None):  # pragma: no cover
        raise AssertionError("compose_suggestions must not run on an interrupt")

    monkeypatch.setattr(nodes, "compose_suggestions", fail_compose)
    payload = {"kind": "confirm_cancel", "question": "আপনি কি নিশ্চিত?",
               "appointment": {"id": "a1", "label": "কার্ডিওলজি"}}
    graph = _FakeGraph(
        script=[],
        final_values={},
        updates=[("__interrupt__", (SimpleNamespace(value=payload),))],
    )
    events = []
    async for ev in stream_turn_tokens(graph, "sess-1", "hi", suggest=True):
        events.append(ev)
    types = [e["type"] for e in events]
    assert "confirm" in types
    confirm = next(e for e in events if e["type"] == "confirm")
    assert confirm["question"] == "আপনি কি নিশ্চিত?"
    assert confirm["kind"] == "confirm_cancel"
    assert "suggestions" not in types
    assert events[-1]["type"] == "end"
    assert events[-1]["done"] is False


@pytest.mark.asyncio
async def test_free_text_on_interrupted_thread_resumes_graph():
    """Typed/spoken yes-or-no on a paused thread becomes Command(resume=bool)."""
    from langgraph.types import Command

    for text, expected in (("হ্যাঁ, নিশ্চিত", True), ("না থাক", False)):
        graph = _FakeGraph(
            script=[("ঠিক আছে।", {"langgraph_node": "call_model"})],
            final_values={},
            interrupts=(SimpleNamespace(value={"question": "?"}),),
        )
        events = []
        async for ev in stream_turn_tokens(graph, "sess-1", text):
            events.append(ev)
        assert isinstance(graph.last_input, Command)
        assert graph.last_input.resume is expected


@pytest.mark.asyncio
async def test_explicit_resume_passes_command():
    """resume=True from the chat confirm button drives Command(resume=True)."""
    from langgraph.types import Command

    graph = _FakeGraph(
        script=[("বাতিল হয়েছে।", {"langgraph_node": "call_model"})],
        final_values={},
        interrupts=(SimpleNamespace(value={"question": "?"}),),
    )
    events = []
    async for ev in stream_turn_tokens(graph, "sess-1", "", resume=True):
        events.append(ev)
    assert isinstance(graph.last_input, Command)
    assert graph.last_input.resume is True


def test_confirmed_parses_resume_values():
    from agent.tools import _confirmed

    assert _confirmed(True) is True
    assert _confirmed(False) is False
    assert _confirmed("হ্যাঁ") is True
    assert _confirmed("না") is False
    assert _confirmed(None) is False


def test_parse_suggestion_lines():
    """Chip parsing: strips bullets/numbering/quotes, drops NONE and over-long
    lines, caps at 3."""
    from agent.nodes import _parse_suggestion_lines

    raw = '১. অ্যাপয়েন্টমেন্ট নিতে চাই\n- "আমার অ্যাপয়েন্টমেন্ট দেখুন"\n• নতুন বুকিং\nবাতিল করুন'
    assert _parse_suggestion_lines(raw) == [
        "অ্যাপয়েন্টমেন্ট নিতে চাই", "আমার অ্যাপয়েন্টমেন্ট দেখুন", "নতুন বুকিং",
    ]
    assert _parse_suggestion_lines("NONE") == []
    assert _parse_suggestion_lines("") == []
    # A rambling sentence is not a chip.
    assert _parse_suggestion_lines("আমি মনে করি রোগী সম্ভবত এরপর জানতে চাইবেন যে " * 3) == []


@pytest.mark.asyncio
async def test_compose_suggestions_extends_thread_prompt(monkeypatch):
    """compose_suggestions invokes the tool-bound model over the thread's own
    messages + one internal instruction, and parses the reply into chips."""
    import agent.nodes as nodes

    captured = {}

    class _FakeLLM:
        async def ainvoke(self, msgs):
            captured["msgs"] = msgs
            return AIMessage(content="আমার অ্যাপয়েন্টমেন্ট দেখুন\nনতুন বুকিং")

    async def fake_system(state, store=None):
        captured["store"] = store
        return "SYSTEM"

    monkeypatch.setattr(nodes, "build_system_prompt", fake_system)
    monkeypatch.setattr(nodes, "_llm_suggest", lambda rag, manage, search=False: _FakeLLM())
    monkeypatch.setattr(nodes, "_hospital_has_docs_sync", lambda hid: False)

    state = {"messages": [HumanMessage(content="hi"), AIMessage(content="হ্যালো!")],
             "patient_account_id": 1}
    items = await nodes.compose_suggestions(state, store="the-store")
    assert items == ["আমার অ্যাপয়েন্টমেন্ট দেখুন", "নতুন বুকিং"]
    assert captured["store"] == "the-store"
    # Prompt = system + full thread history + internal instruction (cache-extending).
    msgs = captured["msgs"]
    assert msgs[0].content == "SYSTEM"
    assert msgs[1].content == "hi" and msgs[2].content == "হ্যালো!"
    assert "INTERNAL UI REQUEST" in msgs[-1].content


@pytest.mark.asyncio
async def test_compose_suggestions_failure_returns_no_chips(monkeypatch):
    """Any failure (LLM error, tool-call reply, empty state) → [] — never canned chips."""
    import agent.nodes as nodes

    assert await nodes.compose_suggestions({"messages": []}) == []

    class _ToolCallLLM:
        async def ainvoke(self, msgs):
            return AIMessage(content="", tool_calls=[
                {"name": "get_available_slots", "args": {}, "id": "x", "type": "tool_call"}])

    async def fake_system(state, store=None):
        return "SYSTEM"

    monkeypatch.setattr(nodes, "build_system_prompt", fake_system)
    monkeypatch.setattr(nodes, "_llm_suggest", lambda rag, manage, search=False: _ToolCallLLM())
    state = {"messages": [HumanMessage(content="hi")]}
    assert await nodes.compose_suggestions(state) == []


def test_turn_input_includes_doctor_id_when_given():
    from agent.runner import _turn_input

    payload = _turn_input("hi", 1, doctor_id=7)
    assert payload["doctor_id"] == 7


def test_turn_input_omits_doctor_id_when_none():
    from agent.runner import _turn_input

    payload = _turn_input("hi", 1)
    assert "doctor_id" not in payload


@pytest.mark.asyncio
async def test_stream_turn_tokens_forwards_doctor_id_into_graph_input():
    graph = _FakeGraph(script=[], final_values={})
    async for _ in stream_turn_tokens(graph, "sess-1", "hi", clinic_id=2, doctor_id=9):
        pass
    assert graph.last_input["doctor_id"] == 9


async def test_partial_datetime_marker_is_never_streamed_mid_build():
    """A slot's [datetime=...] tag arriving split across chunks must never
    reach the patient — regression test for the leaked "[datetime" fragments
    seen when the marker's opening half streams before its closing "]"."""
    script = [
        ("১. সকাল ৯টা ", {"langgraph_node": "call_model"}),
        ("[date", {"langgraph_node": "call_model"}),
        ("time=2026-07-05T09:00:00+06:00] ", {"langgraph_node": "call_model"}),
        ("২. সকাল ৯টা ৩০", {"langgraph_node": "call_model"}),
    ]
    events = await _collect(_FakeGraph(script=script, final_values={}))
    full_text = "".join(e["text"] for e in events if e["type"] == "token")
    assert "datetime" not in full_text
    assert "[" not in full_text
    assert "১. সকাল ৯টা" in full_text
    assert "২. সকাল ৯টা" in full_text


async def test_datetime_marker_split_at_every_boundary_never_leaks():
    """Same tag, but split one character at a time — the worst case for a
    prefix-matching guard."""
    tag = "[datetime=2026-07-05T09:00:00+06:00]"
    script = [(ch, {"langgraph_node": "call_model"}) for ch in f"স্লট {tag} নিন"]
    events = await _collect(_FakeGraph(script=script, final_values={}))
    full_text = "".join(e["text"] for e in events if e["type"] == "token")
    assert "datetime" not in full_text
    assert "[" not in full_text


async def test_second_call_model_hop_after_tools_is_not_suppressed():
    """The ReAct loop can run call_model twice in one turn (announce -> tool
    -> react to result). If the first hop streamed real text, a SECOND,
    different AIMessage from call_model later in the same turn — e.g. a
    deterministic fallback set post-hoc, never streamed token-by-token —
    must still reach the patient, not be swallowed as 'already spoken'."""
    graph = _FakeGraph(
        script=[("এখন স্লট দেখছি।", {"langgraph_node": "call_model"})],
        final_values={},
        updates=[
            ("tools", {"messages": [ToolMessage(content="NO_SLOTS_AVAILABLE", tool_call_id="x")]}),
            ("call_model", {"messages": [AIMessage(content="দুঃখিত, কোনো স্লট নেই।")]}),
        ],
    )
    events = await _collect(graph)
    tokens = "".join(e["text"] for e in events if e["type"] == "token")
    assert "এখন স্লট দেখছি।" in tokens
    assert "দুঃখিত, কোনো স্লট নেই।" in tokens


# --------------------------------------------------------------------------- #
# Failure path: no canned patient text, ever. A turn that dies before speaking
# gets an LLM-composed recovery line; if even that fails, the stream ends
# without an `end` event so the client shows its retry affordance.
# --------------------------------------------------------------------------- #


class _ExplodingGraph(_FakeGraph):
    """Replays `script` like _FakeGraph, then raises (e.g. an Ollama timeout)."""

    def astream(self, _input, *, config, stream_mode):
        parent = super().astream(_input, config=config, stream_mode=stream_mode)

        async def gen():
            async for item in parent:
                yield item
            raise RuntimeError("simulated ollama timeout")

        return gen()


async def test_turn_failure_before_speech_streams_llm_composed_recovery(monkeypatch):
    from unittest.mock import AsyncMock

    import agent.nodes as nodes

    monkeypatch.setattr(
        nodes, "compose_error_reply",
        AsyncMock(return_value="একটু সমস্যা হয়েছে, আবার বলবেন কি?"),
    )
    events = await _collect(_ExplodingGraph([], {}))

    tokens = "".join(e["text"] for e in events if e["type"] == "token")
    assert tokens == "একটু সমস্যা হয়েছে, আবার বলবেন কি?"
    end = events[-1]
    assert end["type"] == "end" and end["done"] is False


async def test_turn_failure_with_dead_llm_ends_stream_without_end_event(monkeypatch):
    from unittest.mock import AsyncMock

    import agent.nodes as nodes

    monkeypatch.setattr(nodes, "compose_error_reply", AsyncMock(return_value=""))
    events = await _collect(_ExplodingGraph([], {}))

    # No fabricated speech and no `end` event — the client treats the turn as
    # failed (web UI drops the empty bubble and offers retry). Only transient
    # status chrome (e.g. the initial "ভাবছি...") may have been emitted.
    assert all(e["type"] == "status" for e in events)


async def test_turn_failure_after_partial_speech_keeps_text_no_recovery(monkeypatch):
    from unittest.mock import AsyncMock

    import agent.nodes as nodes

    compose = AsyncMock(return_value="ব্যবহার হওয়ার কথা নয়")
    monkeypatch.setattr(nodes, "compose_error_reply", compose)
    script = [("আপনার নাম", {"langgraph_node": "call_model"})]
    events = await _collect(_ExplodingGraph(script, {}))

    tokens = "".join(e["text"] for e in events if e["type"] == "token")
    assert tokens == "আপনার নাম"
    compose.assert_not_awaited()  # partial reply already reached the patient
    assert events[-1]["type"] == "end" and events[-1]["done"] is False


async def test_fabricated_pre_tool_listing_is_retracted():
    """Live-observed 2026-07-08: the model streamed an invented numbered slot
    list and THEN emitted the get_available_slots call in the same response.
    The listing had already reached the patient token-by-token, so the runner
    must yield a retract event telling the UI to drop it, and the real
    post-tool reply must stream cleanly afterwards."""
    fabricated = "১. সকাল ৯টা ২. সকাল ১০টা — কোনটি নেবেন?"
    real_reply = "এই সময়গুলো ফাঁকা আছে।"
    graph = _FakeGraph(
        script=[
            (fabricated, {"langgraph_node": "call_model"}),
            ("", {"langgraph_node": "call_model"},
             [{"name": "get_available_slots", "args": "", "id": "t1", "index": 0}]),
        ],
        final_values={},
        updates=[
            ("tools", {"messages": [ToolMessage(content="AVAILABLE_SLOTS: …", tool_call_id="t1")]}),
            ("call_model", {"messages": [AIMessage(content=real_reply)]}),
        ],
    )
    events = await _collect(graph)

    retracts = [e for e in events if e["type"] == "retract"]
    assert len(retracts) == 1
    assert retracts[0]["keep"] == 0  # nothing spoken before this hop → drop all

    # Replay what the UI would show: tokens append, retract slices to `keep`.
    shown = ""
    for e in events:
        if e["type"] == "token":
            shown += e["text"]
        elif e["type"] == "retract":
            shown = shown[: e["keep"]]
    assert shown == real_reply
    assert "৯টা" not in shown  # no trace of the invented list survives


async def test_pre_tool_announce_without_listing_is_not_retracted():
    """Plain 'দেখছি…' announce text before a tool call is legitimate speech —
    the retract guard must fire only on invented listing data."""
    graph = _FakeGraph(
        script=[
            ("আমি এখন সময়গুলো দেখছি।", {"langgraph_node": "call_model"}),
            ("", {"langgraph_node": "call_model"},
             [{"name": "get_available_slots", "args": "", "id": "t1", "index": 0}]),
        ],
        final_values={},
        updates=[("tools", {"messages": [ToolMessage(content="…", tool_call_id="t1")]})],
    )
    events = await _collect(graph)
    assert not any(e["type"] == "retract" for e in events)
    tokens = "".join(e["text"] for e in events if e["type"] == "token")
    assert tokens == "আমি এখন সময়গুলো দেখছি।"


async def test_compose_error_reply_is_llm_text_or_empty(monkeypatch):
    """compose_error_reply returns the model's own words, and '' (never canned
    text, never an exception) when the LLM call fails or comes back empty."""
    from unittest.mock import AsyncMock

    import agent.nodes as nodes

    llm = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(content="  দুঃখিত ভাই। ")))
    monkeypatch.setattr(nodes, "_llm_plain", lambda: llm)
    assert await nodes.compose_error_reply() == "দুঃখিত ভাই।"
    # The instruction goes as the sole human message — tiny prefill by design.
    (messages,), _ = llm.ainvoke.await_args
    assert len(messages) == 1

    llm.ainvoke = AsyncMock(return_value=SimpleNamespace(content=""))
    assert await nodes.compose_error_reply() == ""

    llm.ainvoke = AsyncMock(side_effect=RuntimeError("connection refused"))
    assert await nodes.compose_error_reply() == ""


# ---------------------------------------------------------------------------
# Transcript logging attribution — a unified platform thread may pick a
# hospital/clinic mid-turn (choose_doctor); the logged clinic_id/hospital_id
# must reflect where the thread landed by the END of the turn, not the
# per-turn request's starting clinic_id. And every turn is worth logging now
# (conversation_log.clinic_id is nullable, migration 0025) — including one
# with no clinic at all.
# ---------------------------------------------------------------------------

async def test_log_attributes_to_landed_clinic_not_request_clinic(monkeypatch):
    from unittest.mock import AsyncMock

    log_turn = AsyncMock()
    monkeypatch.setattr("agent.runner.log_turn", log_turn)

    graph = _FakeGraph(
        script=[("ঠিক আছে।", {"langgraph_node": "call_model"})],
        # The turn started platform-mode (no clinic passed to stream_turn_tokens
        # below) but choose_doctor landed it on clinic 7 / hospital 3 mid-turn.
        final_values={"clinic_id": 7, "hospital_id": 3},
    )
    async for _ in stream_turn_tokens(graph, "pt-acc1-platform", "hai"):
        pass

    assert log_turn.await_count == 2  # user turn + assistant turn
    for call in log_turn.await_args_list:
        assert call.kwargs["clinic_id"] == 7
        assert call.kwargs["hospital_id"] == 3


async def test_log_still_fires_with_no_clinic_at_all(monkeypatch):
    from unittest.mock import AsyncMock

    log_turn = AsyncMock()
    monkeypatch.setattr("agent.runner.log_turn", log_turn)

    graph = _FakeGraph(
        script=[("নমস্কার।", {"langgraph_node": "call_model"})],
        final_values={},  # still platform-mode, no department chosen yet
    )
    async for _ in stream_turn_tokens(graph, "pt-acc1-platform", "hai"):
        pass

    assert log_turn.await_count == 2
    for call in log_turn.await_args_list:
        assert call.kwargs["clinic_id"] is None
        assert call.kwargs["hospital_id"] is None
