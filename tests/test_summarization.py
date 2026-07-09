"""Tests for long-thread compaction (agent/nodes.py) and stable voice thread ids."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent import nodes
from agent.nodes import (
    _KEEP_RECENT,
    _SUMMARIZE_AFTER,
    _summarize_history,
    _summary_cut_index,
    _summary_section,
    _transcript_for_summary,
)


def _chat(n_pairs: int) -> list:
    msgs = []
    for i in range(n_pairs):
        msgs.append(HumanMessage(content=f"রোগীর বার্তা {i}", id=f"h{i}"))
        msgs.append(AIMessage(content=f"এজেন্টের উত্তর {i}", id=f"a{i}"))
    return msgs


# --- _summary_cut_index -----------------------------------------------------

def test_cut_lands_on_human_message():
    msgs = _chat(16)  # 32 messages
    cut = _summary_cut_index(msgs)
    assert cut >= len(msgs) - _KEEP_RECENT
    assert isinstance(msgs[cut], HumanMessage)


def test_cut_never_orphans_tool_message():
    # Tail is AI(tool_calls) + ToolMessage + AI — cut must skip past them to
    # the next HumanMessage rather than starting kept history on a ToolMessage.
    msgs = _chat(10)
    msgs += [
        AIMessage(content="", tool_calls=[{"name": "t", "args": {}, "id": "c1"}], id="atc"),
        ToolMessage(content="RESULT", tool_call_id="c1", id="t1"),
        AIMessage(content="ফলাফল জানালাম", id="af"),
        HumanMessage(content="আচ্ছা", id="hl"),
        AIMessage(content="জি", id="al"),
    ]
    cut = _summary_cut_index(msgs)
    assert isinstance(msgs[cut], HumanMessage)
    kept = msgs[cut:]
    assert not any(isinstance(m, ToolMessage) for m in kept[:1])


def test_cut_returns_len_when_no_human_boundary():
    msgs = [AIMessage(content=f"a{i}", id=f"a{i}") for i in range(30)]
    assert _summary_cut_index(msgs) == len(msgs)  # caller skips compaction


# --- _transcript_for_summary / _summary_section ------------------------------

def test_transcript_includes_roles_and_skips_tool_call_stubs():
    msgs = [
        HumanMessage(content="নাম রহিম"),
        AIMessage(content="", tool_calls=[{"name": "t", "args": {}, "id": "c"}]),
        ToolMessage(content="BOOKED: appointment_id=x", tool_call_id="c"),
        AIMessage(content="বুক হয়েছে"),
    ]
    text = _transcript_for_summary(msgs)
    assert "রোগী: নাম রহিম" in text
    assert "[tool result] BOOKED" in text
    assert "এজেন্ট: বুক হয়েছে" in text


def test_summary_section_empty_for_no_summary():
    assert _summary_section(None) == ""
    assert _summary_section("  ") == ""
    assert "সারাংশ-টেক্সট" in _summary_section("সারাংশ-টেক্সট")


# --- _summarize_history -----------------------------------------------------

def _fake_llm(reply: str):
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=AIMessage(content=reply))
    return llm


def test_summarize_below_threshold_is_noop(monkeypatch):
    state = {"messages": _chat(4)}
    result = asyncio.run(_summarize_history(state))
    assert result is None


def test_summarize_compacts_old_messages(monkeypatch):
    llm = _fake_llm("রোগী রহিম, বয়স ৪০, কার্ডিওলজি বিভাগে বুকিং সম্পন্ন।")
    monkeypatch.setattr(nodes, "_llm_plain", lambda: llm)
    msgs = _chat(_SUMMARIZE_AFTER // 2 + 4)
    state = {"messages": msgs, "conversation_summary": "আগের সারাংশ।"}

    result = asyncio.run(_summarize_history(state))
    assert result is not None
    assert result["summary"].startswith("রোগী রহিম")
    # Removals cover exactly the messages not kept.
    assert len(result["removals"]) == len(msgs) - len(result["kept"])
    assert len(result["kept"]) <= _KEEP_RECENT
    assert isinstance(result["kept"][0], HumanMessage)
    # Prior summary was fed into the compaction prompt.
    sent = llm.ainvoke.call_args.args[0]
    assert "আগের সারাংশ।" in sent[1].content


def test_summarize_empty_llm_reply_is_noop(monkeypatch):
    monkeypatch.setattr(nodes, "_llm_plain", lambda: _fake_llm("   "))
    state = {"messages": _chat(_SUMMARIZE_AFTER)}
    assert asyncio.run(_summarize_history(state)) is None


# --- voice thread id ----------------------------------------------------------

def test_voice_session_id_matches_portal_chat_thread():
    pytest.importorskip("livekit.agents")
    from main import _voice_session_id

    scope = {"clinic_id": 3, "hospital_id": 1, "patient": {"account_id": 7}}
    assert _voice_session_id(scope) == "pt-acc7-clinic3"


def test_voice_session_id_hospital_level():
    pytest.importorskip("livekit.agents")
    from main import _voice_session_id

    scope = {"clinic_id": None, "hospital_id": 5, "patient": {"account_id": 7}}
    assert _voice_session_id(scope) == "pt-acc7-hosp5"


def test_voice_session_id_anonymous_is_unique():
    pytest.importorskip("livekit.agents")
    from main import _voice_session_id

    scope = {"clinic_id": 3, "hospital_id": None, "patient": None}
    a, b = _voice_session_id(scope), _voice_session_id(scope)
    assert a.startswith("voice-") and a != b
