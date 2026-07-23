"""Tests for agent/nodes.py::_prompt_context's doctor_context branch.

Covers the pre-selected-doctor path added so a patient who picked a doctor in
the portal wizard before starting the chat doesn't get asked again, and the
model is given facts (not a canned sentence) to weave into its own greeting.
"""

import pytest

from agent import nodes


async def test_multi_doctor_asks_when_none_selected(monkeypatch):
    async def fake_doctors(clinic_id):
        return [{"id": 1, "name": "Rahim"}, {"id": 2, "name": "Karim"}]

    monkeypatch.setattr(nodes, "get_doctors_for_clinic", fake_doctors)
    state = {"clinic_id": 2, "messages": []}
    ctx = await nodes._prompt_context(state, {})
    assert "MULTI-DOCTOR MODE" in ctx["doctor_context"]


async def test_single_doctor_clinic_stays_silent_when_none_selected(monkeypatch):
    async def fake_doctors(clinic_id):
        return [{"id": 1, "name": "Rahim"}]

    monkeypatch.setattr(nodes, "get_doctors_for_clinic", fake_doctors)
    state = {"clinic_id": 2, "messages": []}
    ctx = await nodes._prompt_context(state, {})
    assert ctx["doctor_context"] == ""


async def test_preselected_doctor_mentions_name_and_skips_multi_doctor(monkeypatch):
    async def fake_get_doctor(doctor_id, clinic_id=None):
        assert doctor_id == 5 and clinic_id == 2
        return {"id": 5, "name": "Rahim"}

    monkeypatch.setattr(nodes, "get_doctor", fake_get_doctor)
    state = {"clinic_id": 2, "doctor_id": 5, "messages": []}
    ctx = await nodes._prompt_context(state, {})
    assert "PRE-SELECTED DOCTOR" in ctx["doctor_context"]
    assert "Rahim" in ctx["doctor_context"]
    assert "MULTI-DOCTOR MODE" not in ctx["doctor_context"]
    assert "in your own words" in ctx["doctor_context"]  # not a fixed script
    # Long-lived unified threads: keyed on (re)opening the chat, not "first message".
    assert "(re)opens the chat" in ctx["doctor_context"]  # overrides the "wait" gate


async def test_preselected_doctor_includes_hospital_name_when_available(monkeypatch):
    async def fake_get_doctor(doctor_id, clinic_id=None):
        return {"id": 5, "name": "Rahim"}

    async def fake_get_hospital(hospital_id):
        return {"id": hospital_id, "name": "City Hospital"}

    monkeypatch.setattr(nodes, "get_doctor", fake_get_doctor)
    monkeypatch.setattr(nodes, "get_hospital", fake_get_hospital)
    state = {"clinic_id": 2, "hospital_id": 9, "doctor_id": 5, "messages": []}
    ctx = await nodes._prompt_context(state, {})
    assert "City Hospital" in ctx["doctor_context"]


async def test_preselected_doctor_includes_profile_facts(monkeypatch):
    """Degrees, specialty, department (= clinic name) and the admin-written
    description all reach the prompt so the model can answer questions about
    the doctor from real data instead of inventing."""
    async def fake_get_doctor(doctor_id, clinic_id=None):
        return {
            "id": 5, "name": "Rahim", "degrees": "MBBS, FCPS (Medicine)",
            "specialty": "Cardiology",
            "description": "১৫ বছরের অভিজ্ঞতা। হৃদরোগ বিশেষজ্ঞ।",
        }

    async def fake_get_hospital(hospital_id):
        return {"id": hospital_id, "name": "City Hospital"}

    monkeypatch.setattr(nodes, "get_doctor", fake_get_doctor)
    monkeypatch.setattr(nodes, "get_hospital", fake_get_hospital)
    state = {"clinic_id": 2, "hospital_id": 9, "doctor_id": 5, "messages": []}
    ctx = await nodes._prompt_context(state, {"name": "কার্ডিওলজি"})
    dc = ctx["doctor_context"]
    assert "MBBS, FCPS (Medicine)" in dc
    assert "(Cardiology)" in dc
    assert "কার্ডিওলজি" in dc  # department = clinic name from cfg
    assert "City Hospital" in dc
    assert "হৃদরোগ বিশেষজ্ঞ" in dc
    assert "in your own words" in dc  # profile is background, never a script


async def test_preselected_doctor_description_truncated(monkeypatch):
    async def fake_get_doctor(doctor_id, clinic_id=None):
        return {"id": 5, "name": "Rahim", "description": "word " * 200}

    monkeypatch.setattr(nodes, "get_doctor", fake_get_doctor)
    state = {"clinic_id": 2, "doctor_id": 5, "messages": []}
    ctx = await nodes._prompt_context(state, {})
    dc = ctx["doctor_context"]
    assert "Doctor profile" in dc
    # ~300 chars of profile text plus the ellipsis, not the full kilobyte.
    profile_part = dc.split("Doctor profile", 1)[1]
    assert len(profile_part) < 500
    assert "…" in profile_part


async def test_preselected_doctor_without_profile_has_no_profile_section(monkeypatch):
    async def fake_get_doctor(doctor_id, clinic_id=None):
        return {"id": 5, "name": "Rahim", "degrees": "", "description": ""}

    monkeypatch.setattr(nodes, "get_doctor", fake_get_doctor)
    state = {"clinic_id": 2, "doctor_id": 5, "messages": []}
    ctx = await nodes._prompt_context(state, {})
    assert "Doctor profile" not in ctx["doctor_context"]


async def test_preselected_doctor_degrades_silently_when_stale(monkeypatch):
    async def fake_get_doctor(doctor_id, clinic_id=None):
        return None  # deleted / mismatched doctor

    monkeypatch.setattr(nodes, "get_doctor", fake_get_doctor)
    state = {"clinic_id": 2, "doctor_id": 999, "messages": []}
    ctx = await nodes._prompt_context(state, {})
    assert ctx["doctor_context"] == ""


# ---------------------------------------------------------------------------
# _corrective_instruction / _corrective_reply — broken model responses (empty
# reply, or repeating the same failing tool call) trigger a tool-free LLM
# retry that composes the recovery text itself. NOTHING patient-facing is
# hardcoded (user requirement: fully agentic, no static answers).
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class _FakeToolMessage:
    type = "tool"
    def __init__(self, content):
        self.content = content


def test_corrective_none_for_healthy_tool_call():
    response = _FakeResponse(tool_calls=[{"name": "get_available_slots", "args": {}, "id": "x"}])
    assert nodes._corrective_instruction(response, {"messages": []}) is None
    assert response.tool_calls  # untouched


def test_corrective_none_for_normal_spoken_reply():
    response = _FakeResponse(content="আপনার নাম কী?")
    assert nodes._corrective_instruction(response, {"messages": []}) is None


def test_corrective_fires_on_empty_reply_and_mentions_tool_result():
    response = _FakeResponse(content="")
    state = {"messages": [_FakeToolMessage("NO_SLOTS_AVAILABLE")]}
    instruction = nodes._corrective_instruction(response, state)
    assert instruction is not None
    assert "NO_SLOTS_AVAILABLE" in instruction
    assert "Bangla" in instruction


def test_corrective_fires_on_repeated_identical_tool_call():
    prev = _FakeResponse(tool_calls=[{"name": "search_hospital_info", "args": {"query": "ভিজিটিং"}, "id": "a"}])
    prev.type = "ai"
    state = {"messages": [prev, _FakeToolMessage("NO_INFO: not found")]}
    response = _FakeResponse(
        tool_calls=[{"name": "search_hospital_info", "args": {"query": "ভিজিটিং"}, "id": "b"}]
    )
    instruction = nodes._corrective_instruction(response, state)
    assert instruction is not None
    assert "NO_INFO" in instruction
    assert response.tool_calls == []  # repeat stripped — retry must speak


def test_corrective_allows_same_tool_with_different_args():
    prev = _FakeResponse(tool_calls=[{"name": "search_hospital_info", "args": {"query": "ভিজিটিং"}, "id": "a"}])
    prev.type = "ai"
    state = {"messages": [prev, _FakeToolMessage("NO_INFO: not found")]}
    response = _FakeResponse(
        tool_calls=[{"name": "search_hospital_info", "args": {"query": "পার্কিং"}, "id": "b"}]
    )
    assert nodes._corrective_instruction(response, state) is None
    assert response.tool_calls  # genuinely new query allowed through


async def test_corrective_reply_uses_llm_text_not_canned(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=_FakeResponse(content="দুঃখিত, এখন কোনো তথ্য পাইনি।"))
    monkeypatch.setattr(nodes, "_llm_plain", lambda: llm)
    response = _FakeResponse(content="")
    out = await nodes._corrective_reply("instr", "system prompt", [], response)
    assert out.content == "দুঃখিত, এখন কোনো তথ্য পাইনি।"
    # The corrective instruction is injected into the system message.
    sent_system = llm.ainvoke.call_args.args[0][0].content
    assert "IMPORTANT CORRECTION: instr" in sent_system


async def test_corrective_reply_raises_when_retry_also_empty(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=_FakeResponse(content="  "))
    monkeypatch.setattr(nodes, "_llm_plain", lambda: llm)
    with pytest.raises(RuntimeError):
        await nodes._corrective_reply("instr", "system", [], _FakeResponse(content=""))


# ---------------------------------------------------------------------------
# _force_rag_lookup — model announces an info lookup without calling the tool
# (live-observed: patient asked visiting hours, model said "আমি তথ্য খুঁজে
# দিতে পারি" and never called search_hospital_info).
# ---------------------------------------------------------------------------

class _FakeHuman:
    type = "human"
    def __init__(self, content):
        self.content = content


def test_force_rag_lookup_injects_tool_call(monkeypatch):
    monkeypatch.setitem(nodes._has_docs_cache, 9, True)
    response = _FakeResponse(content="আমি তথ্য খুঁজে দিতে পারি।")
    state = {"messages": [_FakeHuman("ভিজিটিং আওয়ার কি?")]}
    out = nodes._force_rag_lookup(response, state, 9)
    assert out.tool_calls and out.tool_calls[0]["name"] == "search_hospital_info"
    assert out.tool_calls[0]["args"]["query"] == "ভিজিটিং আওয়ার কি?"


def test_force_rag_lookup_noop_when_hospital_has_no_docs(monkeypatch):
    monkeypatch.setitem(nodes._has_docs_cache, 9, False)
    response = _FakeResponse(content="আমি তথ্য খুঁজে দিতে পারি।")
    state = {"messages": [_FakeHuman("ভিজিটিং আওয়ার কি?")]}
    out = nodes._force_rag_lookup(response, state, 9)
    assert not out.tool_calls


def test_force_rag_lookup_noop_when_already_called_tool(monkeypatch):
    monkeypatch.setitem(nodes._has_docs_cache, 9, True)
    response = _FakeResponse(tool_calls=[{"name": "search_hospital_info", "args": {}, "id": "x"}])
    out = nodes._force_rag_lookup(response, {"messages": []}, 9)
    assert out.tool_calls[0]["name"] == "search_hospital_info"  # untouched, not re-forced


def test_force_rag_lookup_noop_on_unrelated_reply(monkeypatch):
    monkeypatch.setitem(nodes._has_docs_cache, 9, True)
    response = _FakeResponse(content="আপনার নাম কী?")
    state = {"messages": [_FakeHuman("আমার নাম রহিম")]}
    out = nodes._force_rag_lookup(response, state, 9)
    assert not out.tool_calls


def test_force_rag_lookup_fires_in_platform_mode_without_docs(monkeypatch):
    """search_hospital_info is bound PERMANENTLY in platform mode — the guard
    must fire on an info question even when has_docs is False/unset (the
    tool itself degrades to NO_INFO or a cross-hospital search), unlike
    department mode where the has-docs gate matches the conditional binding."""
    monkeypatch.setitem(nodes._has_docs_cache, 9, False)
    response = _FakeResponse(content="আমি তথ্য খুঁজে দিতে পারি।")
    state = {
        "platform_mode": True,
        "messages": [_FakeHuman("ভিজিটিং আওয়ার কি?")],
    }
    out = nodes._force_rag_lookup(response, state, 9)
    assert out.tool_calls and out.tool_calls[0]["name"] == "search_hospital_info"


def test_force_rag_lookup_fires_in_platform_mode_with_no_hospital_yet(monkeypatch):
    """No hospital chosen at all (hospital_id=None) — still forces the call;
    the tool then falls back to a cross-hospital search."""
    response = _FakeResponse(content="আমি তথ্য খুঁজে দিতে পারি।")
    state = {
        "platform_mode": True,
        "messages": [_FakeHuman("ভিজিটিং আওয়ার কি?")],
    }
    out = nodes._force_rag_lookup(response, state, None)
    assert out.tool_calls and out.tool_calls[0]["name"] == "search_hospital_info"


def test_force_rag_lookup_catches_who_is_question(monkeypatch):
    monkeypatch.setitem(nodes._has_docs_cache, 9, True)
    response = _FakeResponse(content="দুঃখিত, আমি জানি না।")
    state = {"messages": [_FakeHuman("who is Rakibul Haque?")]}
    out = nodes._force_rag_lookup(response, state, 9)
    assert out.tool_calls and out.tool_calls[0]["args"]["query"] == "who is Rakibul Haque?"


def test_force_rag_lookup_catches_bangla_ke_question(monkeypatch):
    monkeypatch.setitem(nodes._has_docs_cache, 9, True)
    response = _FakeResponse(content="আমি নিশ্চিত নই।")
    state = {"messages": [_FakeHuman("রাকিবুল হক কে?")]}
    out = nodes._force_rag_lookup(response, state, 9)
    assert out.tool_calls


def test_history_for_retry_cuts_trailing_tool_messages():
    human = _FakeHuman("who is Rakibul Haque?")
    ai_with_call = _FakeResponse(tool_calls=[{"name": "t", "args": {}, "id": "a"}])
    ai_with_call.type = "ai"
    tool = _FakeToolMessage("HOSPITAL_INFO: ...")
    out = nodes._history_for_retry([human, ai_with_call, tool])
    assert out == [human]


def test_history_for_retry_keeps_full_history_when_no_human():
    ai = _FakeResponse(content="hi")
    ai.type = "ai"
    out = nodes._history_for_retry([ai])
    assert out == [ai]


# ---------------------------------------------------------------------------
# Speed package: prompt ordering (KV-cache reuse), build_system_prompt outside
# a graph run, and prewarm_turn semantics.
# ---------------------------------------------------------------------------

def test_dynamic_prompt_sections_are_at_the_tail():
    """patient_context/summary_context vary per turn — they must sit AFTER all
    stable sections so Ollama's prompt-prefix cache survives their changes."""
    from agent.prompts import SYSTEM_PROMPT

    for stable in ("{ivr_context}", "{doctor_context}", "{greeting_context}",
                   "{rag_context}", "{manage_context}"):
        assert SYSTEM_PROMPT.index(stable) < SYSTEM_PROMPT.index("{patient_context}")
    assert SYSTEM_PROMPT.index("{patient_context}") < SYSTEM_PROMPT.index("{summary_context}")


async def test_build_system_prompt_outside_graph_run(monkeypatch):
    """Prewarm calls this outside any graph run: get_store() raises there and
    must degrade to an empty patient section, not crash."""
    state = {"clinic_id": 2, "patient_account_id": 7, "messages": []}

    async def fake_doctors(clinic_id):
        return [{"id": 1, "name": "Rahim"}]

    monkeypatch.setattr(nodes, "get_doctors_for_clinic", fake_doctors)
    monkeypatch.setattr(nodes, "_clinic_cache", {})

    async def fake_clinic(cid):
        return {"name": "Test Clinic", "doctor_name": "Rahim"}

    monkeypatch.setattr(nodes, "get_clinic", fake_clinic)
    system = await nodes.build_system_prompt(state)
    assert "Test Clinic" in system
    assert "{patient_context}" not in system  # fully formatted


async def test_prewarm_skips_when_semaphore_busy(monkeypatch):
    from unittest.mock import MagicMock

    called = MagicMock()
    monkeypatch.setattr(nodes, "_llm_for", called)
    sem = nodes._get_semaphore()
    async with sem:
        # exhaust remaining slots so .locked() is True
        while not sem.locked():
            await sem.acquire()
        await nodes.prewarm_turn({"clinic_id": 2})
        while sem._value < nodes.settings.ollama_max_concurrent - 1:
            sem.release()
    called.assert_not_called()


async def test_prewarm_invokes_bound_model_when_free(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    async def fake_system(state):
        return "SYSTEM"

    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=MagicMock(content="ok"))
    captured = {}

    def fake_prewarm_llm(rag, manage, search=False):
        captured["rag"], captured["manage"] = rag, manage
        return llm

    monkeypatch.setattr(nodes, "build_system_prompt", fake_system)
    monkeypatch.setattr(nodes, "_llm_prewarm", fake_prewarm_llm)
    await nodes.prewarm_turn({"clinic_id": 2, "patient_account_id": 7})
    llm.ainvoke.assert_awaited_once()
    sent = llm.ainvoke.call_args.args[0]
    assert sent[0].content == "SYSTEM"
    assert sent[1].content == ""
    assert captured["manage"] is True  # same binding a real turn would use


def test_prewarm_model_caps_generation_to_one_token():
    llm = nodes._llm_prewarm(rag=False, manage=False)
    # bound runnable wraps the ChatOllama; the underlying model must have
    # num_predict=1 so prewarm pays prefill only, not a full generation.
    assert llm.bound.num_predict == 1
