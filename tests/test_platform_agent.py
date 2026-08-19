"""Platform (marketplace) mode — the cross-hospital search_doctors tool, scope
propagation through choose_doctor, lazy patient linkage on booking, the
PLATFORM MODE prompt branch, the binding matrix, and the deterministic
search guard. No Postgres/Ollama: everything is mocked (see conftest)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage


SEARCH_ROW = {
    "id": 5, "clinic_id": 2, "hospital_id": 1,
    "name": "Rahim", "degrees": "MBBS, FCPS", "specialty": "Cardiology",
    "description": "", "has_photo": False,
    "hospital_name": "City Hospital", "department_name": "Cardiology",
    "fee_new": 800, "fee_followup": 500,
    "avg_rating": 4.5, "review_count": 12,
}

NEXT_SLOT = {"label": "সোমবার সকাল ৯টা", "datetime": "2026-07-13T09:00:00"}


# ---------------------------------------------------------------------------
# search_doctors tool — data lines and available_doctors scope
# ---------------------------------------------------------------------------

async def test_search_doctors_line_has_hospital_fee_rating_slot(monkeypatch):
    from agent import tools

    monkeypatch.setattr(
        tools, "_search_doctors_platform", AsyncMock(return_value=[dict(SEARCH_ROW)])
    )
    monkeypatch.setattr(
        tools, "_get_available_slots", AsyncMock(return_value=[NEXT_SLOT])
    )
    cmd = await tools.search_doctors.coroutine(
        query="কার্ডিওলজিস্ট", tool_call_id="t1", state={}
    )
    text = cmd.update["messages"][0].content
    assert text.startswith("DOCTORS:")
    assert (
        "1. Rahim, MBBS, FCPS (Cardiology) — City Hospital, Cardiology"
        " | ফি: নতুন ৳800/ফলো-আপ ৳500 | রেটিং 4.5★ (12)"
        " | পরবর্তী ফাঁকা: সোমবার সকাল ৯টা"
    ) in text
    # Entries carry the scope choose_doctor needs to land the thread.
    entry = cmd.update["available_doctors"][0]
    assert entry == {
        "id": 5, "name": "Rahim", "clinic_id": 2, "hospital_id": 1,
        "hospital_name": "City Hospital",
    }


async def test_search_doctors_omits_unset_segments(monkeypatch):
    from agent import tools

    bare = {**SEARCH_ROW, "fee_new": None, "fee_followup": None,
            "avg_rating": 0, "review_count": 0, "degrees": "", "specialty": ""}
    monkeypatch.setattr(
        tools, "_search_doctors_platform", AsyncMock(return_value=[bare])
    )
    monkeypatch.setattr(tools, "_get_available_slots", AsyncMock(return_value=[]))
    cmd = await tools.search_doctors.coroutine(query="rahim", tool_call_id="t1", state={})
    text = cmd.update["messages"][0].content
    assert "1. Rahim — City Hospital, Cardiology" in text
    assert "ফি" not in text and "রেটিং" not in text and "পরবর্তী" not in text


async def test_search_falls_back_bangla_specialty_to_english(monkeypatch):
    # Doctor rows store English specialties; a full Bangla sentence must still
    # land on them via the stem map (কার্ডিও → cardio).
    from agent import tools

    async def fake_search(q="", limit=5):
        return [dict(SEARCH_ROW)] if q == "cardio" else []

    monkeypatch.setattr(tools, "_search_doctors_platform", fake_search)
    monkeypatch.setattr(tools, "_get_available_slots", AsyncMock(return_value=[]))
    cmd = await tools.search_doctors.coroutine(
        query="সবচেয়ে ভালো কার্ডিওলজিস্ট কে? ফি কত?", tool_call_id="t1", state={}
    )
    assert "1. Rahim" in cmd.update["messages"][0].content


async def test_search_falls_back_to_word_tokens(monkeypatch):
    from agent import tools

    async def fake_search(q="", limit=5):
        return [dict(SEARCH_ROW)] if q == "Rahim" else []

    monkeypatch.setattr(tools, "_search_doctors_platform", fake_search)
    monkeypatch.setattr(tools, "_get_available_slots", AsyncMock(return_value=[]))
    cmd = await tools.search_doctors.coroutine(
        query="Dr. Rahim এর ফি কত?", tool_call_id="t1", state={}
    )
    assert "1. Rahim" in cmd.update["messages"][0].content


async def test_search_doctors_empty_returns_marker(monkeypatch):
    from agent import tools

    monkeypatch.setattr(tools, "_search_doctors_platform", AsyncMock(return_value=[]))
    cmd = await tools.search_doctors.coroutine(query="xyz", tool_call_id="t1", state={})
    assert "NO_DOCTORS_FOUND" in cmd.update["messages"][0].content
    assert "available_doctors" not in cmd.update


async def test_search_doctors_slot_failure_degrades_gracefully(monkeypatch):
    from agent import tools

    monkeypatch.setattr(
        tools, "_search_doctors_platform", AsyncMock(return_value=[dict(SEARCH_ROW)])
    )
    monkeypatch.setattr(
        tools, "_get_available_slots", AsyncMock(side_effect=RuntimeError("db down"))
    )
    cmd = await tools.search_doctors.coroutine(query="cardio", tool_call_id="t1", state={})
    text = cmd.update["messages"][0].content
    assert "1. Rahim" in text and "পরবর্তী" not in text


# ---------------------------------------------------------------------------
# choose_doctor — scope propagation from search entries
# ---------------------------------------------------------------------------

async def test_choose_doctor_propagates_clinic_scope():
    from agent import tools

    state = {"available_doctors": [
        {"id": 5, "name": "Rahim", "clinic_id": 2, "hospital_id": 1,
         "hospital_name": "City Hospital"},
    ]}
    cmd = await tools.choose_doctor.coroutine(
        doctor_number=1, tool_call_id="t1", state=state
    )
    assert cmd.update["doctor_id"] == 5
    assert cmd.update["clinic_id"] == 2
    assert cmd.update["hospital_id"] == 1
    assert "(City Hospital)" in cmd.update["messages"][0].content


async def test_choose_doctor_department_mode_unchanged():
    # Entries from list_doctors have no clinic_id — scope must not be touched.
    from agent import tools

    state = {"available_doctors": [{"id": 5, "name": "Rahim"}]}
    cmd = await tools.choose_doctor.coroutine(
        doctor_number=1, tool_call_id="t1", state=state
    )
    assert cmd.update["doctor_id"] == 5
    assert "clinic_id" not in cmd.update and "hospital_id" not in cmd.update


# ---------------------------------------------------------------------------
# book_appointment — lazy patient linkage for platform bookings
# ---------------------------------------------------------------------------

_BOOK_ARGS = dict(
    patient_name="রাহেলা", patient_age=40, patient_mobile="01711000000",
    slot_datetime="2026-07-13T09:00:00", slot_label="সোমবার সকাল ৯টা",
    tool_call_id="t1",
)


async def test_book_appointment_lazily_links_platform_patient():
    from agent import tools

    book = AsyncMock(return_value={"id": "apt-1", "serial_number": 3})
    with (
        patch("agent.tools._resolve_booking_fee", new=AsyncMock(return_value=0)),
        patch("agent.tools._get_hospital_id_for_clinic", new=AsyncMock(return_value=1)),
        patch("agent.tools._get_patient_account",
              new=AsyncMock(return_value={"id": 7, "name": "Kodu", "phone": "01711000000"})),
        patch("agent.tools._get_or_create_patient",
              new=AsyncMock(return_value={"id": 99})) as goc,
        patch("agent.tools._book_appointment", new=book),
        patch("agent.tools.send_booking_confirmation", new=AsyncMock()),
        patch("agent.tools.send_doctor_notification", new=AsyncMock()),
    ):
        cmd = await tools.book_appointment.coroutine(
            **_BOOK_ARGS,
            state={"clinic_id": 2, "patient_account_id": 7, "patient_id": None,
                   "slots_shown": True, "session_id": "s1"},
        )

    goc.assert_awaited_once_with(
        hospital_id=1, name="Kodu", phone="01711000000", account_id=7
    )
    assert book.await_args.kwargs["patient_id"] == 99
    assert cmd.update["patient_id"] == 99
    assert cmd.update["appointment_id"] == "apt-1"


async def test_book_appointment_linkage_failure_still_books():
    from agent import tools

    book = AsyncMock(return_value={"id": "apt-2", "serial_number": 4})
    with (
        patch("agent.tools._resolve_booking_fee", new=AsyncMock(return_value=0)),
        patch("agent.tools._get_hospital_id_for_clinic",
              new=AsyncMock(side_effect=RuntimeError("db down"))),
        patch("agent.tools._book_appointment", new=book),
        patch("agent.tools.send_booking_confirmation", new=AsyncMock()),
        patch("agent.tools.send_doctor_notification", new=AsyncMock()),
    ):
        cmd = await tools.book_appointment.coroutine(
            **_BOOK_ARGS,
            state={"clinic_id": 2, "patient_account_id": 7, "patient_id": None,
                   "slots_shown": True, "session_id": "s1"},
        )

    assert book.await_args.kwargs["patient_id"] is None
    assert cmd.update["appointment_id"] == "apt-2"


async def test_book_appointment_skips_linkage_when_patient_known():
    from agent import tools

    with (
        patch("agent.tools._resolve_booking_fee", new=AsyncMock(return_value=0)),
        patch("agent.tools._get_or_create_patient", new=AsyncMock()) as goc,
        patch("agent.tools._book_appointment",
              new=AsyncMock(return_value={"id": "apt-3", "serial_number": 5})),
        patch("agent.tools.send_booking_confirmation", new=AsyncMock()),
        patch("agent.tools.send_doctor_notification", new=AsyncMock()),
    ):
        await tools.book_appointment.coroutine(
            **_BOOK_ARGS,
            state={"clinic_id": 2, "patient_account_id": 7, "patient_id": 42,
                   "slots_shown": True, "session_id": "s1"},
        )
    goc.assert_not_awaited()


async def test_book_appointment_garbled_mobile_falls_back_to_account_phone():
    # gemma4 sometimes emits an extra digit when transcribing spoken numbers.
    # A logged-in patient registered with a phone — book with that instead of
    # dead-ending the whole flow on INVALID_MOBILE.
    from agent import tools

    book = AsyncMock(return_value={"id": "apt-4", "serial_number": 6})
    with (
        patch("agent.tools._resolve_booking_fee", new=AsyncMock(return_value=0)),
        patch("agent.tools._get_hospital_id_for_clinic", new=AsyncMock(return_value=1)),
        patch("agent.tools._get_patient_account",
              new=AsyncMock(return_value={"id": 7, "name": "Kodu", "phone": "01711000000"})),
        patch("agent.tools._get_or_create_patient",
              new=AsyncMock(return_value={"id": 99})),
        patch("agent.tools._book_appointment", new=book),
        patch("agent.tools.send_booking_confirmation", new=AsyncMock()),
        patch("agent.tools.send_doctor_notification", new=AsyncMock()),
    ):
        cmd = await tools.book_appointment.coroutine(
            **{**_BOOK_ARGS, "patient_mobile": "017110000000"},  # 12 digits
            state={"clinic_id": 2, "patient_account_id": 7, "patient_id": None,
                   "slots_shown": True, "session_id": "s1"},
        )

    assert book.await_args.kwargs["patient_mobile"] == "01711000000"
    assert cmd.update["appointment_id"] == "apt-4"


async def test_book_appointment_invalid_mobile_keeps_slots_shown():
    # No account to fall back on → still INVALID_MOBILE, but the already
    # fetched slot list stays valid (no forced re-fetch loop).
    from agent import tools

    book = AsyncMock()
    with (
        patch("agent.tools._book_appointment", new=book),
        patch("agent.tools.send_booking_confirmation", new=AsyncMock()),
        patch("agent.tools.send_doctor_notification", new=AsyncMock()),
    ):
        cmd = await tools.book_appointment.coroutine(
            **{**_BOOK_ARGS, "patient_mobile": "0171"},
            state={"clinic_id": 2, "slots_shown": True, "session_id": "s1"},
        )

    book.assert_not_awaited()
    assert "INVALID_MOBILE" in cmd.update["messages"][0].content
    assert "slots_shown" not in cmd.update


# ---------------------------------------------------------------------------
# Binding matrix — platform binds search_doctors AND search_hospital_info
# permanently; department mode keeps the conditional has-docs RAG gate.
# ---------------------------------------------------------------------------

def test_binding_flags_platform():
    from agent import nodes

    assert nodes._binding_flags(
        {"platform_mode": True, "patient_account_id": 7}
    ) == (True, True, True)
    assert nodes._binding_flags({"platform_mode": True}) == (True, False, True)


def test_binding_flags_platform_rag_stays_on_regardless_of_docs(monkeypatch):
    from agent import nodes

    # RAG is bound PERMANENTLY on a platform thread (search_hospital_info
    # resolves its own scope at call time, falling back to a cross-hospital
    # search) — has_docs must not flip it either way, or the tool schema
    # (and prompt head) would churn mid-thread when choose_doctor sets
    # hospital_id.
    monkeypatch.setitem(nodes._has_docs_cache, 1, True)
    assert nodes._binding_flags(
        {"platform_mode": True, "hospital_id": 1}
    ) == (True, False, True)
    monkeypatch.setitem(nodes._has_docs_cache, 1, False)
    assert nodes._binding_flags(
        {"platform_mode": True, "hospital_id": 1}
    ) == (True, False, True)


def test_binding_flags_department_mode_has_no_search(monkeypatch):
    from agent import nodes

    monkeypatch.setitem(nodes._has_docs_cache, 1, True)
    assert nodes._binding_flags(
        {"hospital_id": 1, "patient_account_id": 7}
    ) == (True, True, False)


def test_binding_flags_department_mode_still_gated_on_has_docs(monkeypatch):
    from agent import nodes

    monkeypatch.setitem(nodes._has_docs_cache, 1, False)
    assert nodes._binding_flags(
        {"hospital_id": 1, "patient_account_id": 7}
    ) == (False, True, False)


def test_llm_for_platform_binds_search_and_rag_tools():
    from agent import nodes

    llm = nodes._llm_for(None, 7, platform=True)
    names = [t["function"]["name"] for t in llm.kwargs["tools"]]
    assert "search_doctors" in names
    assert "search_hospital_info" in names


def test_llm_for_department_mode_has_no_search_tool():
    from agent import nodes

    llm = nodes._llm_for(None, 7, platform=False)
    names = [t["function"]["name"] for t in llm.kwargs["tools"]]
    assert "search_doctors" not in names


# ---------------------------------------------------------------------------
# Prompt context — the PLATFORM MODE branch
# ---------------------------------------------------------------------------

async def test_prompt_context_platform_branch():
    from agent import nodes
    from config import settings

    ctx = await nodes._prompt_context({"platform_mode": True}, {})
    assert ctx["clinic"] == settings.platform_name
    assert "PLATFORM MODE" in ctx["ivr_context"]
    assert "search_doctors" in ctx["ivr_context"]
    assert "select_department" in ctx["ivr_context"]  # the "do NOT" instruction


async def test_prompt_context_platform_branch_stable_after_clinic_chosen(monkeypatch):
    # After choose_doctor sets clinic_id, the platform head must NOT change
    # (KV cache): same branding, same ivr_context, no HOSPITAL MODE takeover.
    from agent import nodes

    monkeypatch.setattr(nodes, "list_departments", AsyncMock(return_value=[]))
    before = await nodes._prompt_context({"platform_mode": True}, {})
    after = await nodes._prompt_context(
        {"platform_mode": True, "clinic_id": 2, "hospital_id": 1}, {}
    )
    assert after["clinic"] == before["clinic"]
    assert after["ivr_context"] == before["ivr_context"]


# ---------------------------------------------------------------------------
# _force_doctor_search guard — patient-keyed, first hop, platform only
# ---------------------------------------------------------------------------

def _toolless_reply(text="আমি ঠিক জানি না।"):
    return AIMessage(content=text)


def test_force_doctor_search_fires_on_specialist_request():
    from agent import nodes

    state = {
        "platform_mode": True,
        "messages": [HumanMessage(content="সবচেয়ে ভালো কার্ডিওলজিস্ট কে? ফি কত?")],
    }
    out = nodes._force_doctor_search(_toolless_reply(), state)
    assert out.tool_calls and out.tool_calls[0]["name"] == "search_doctors"
    assert out.tool_calls[0]["args"] == {"query": "সবচেয়ে ভালো কার্ডিওলজিস্ট কে? ফি কত?"}


def test_force_doctor_search_ignores_department_mode():
    from agent import nodes

    state = {
        "clinic_id": 2,
        "messages": [HumanMessage(content="ভালো ডাক্তার দরকার")],
    }
    out = nodes._force_doctor_search(_toolless_reply(), state)
    assert not out.tool_calls


def test_force_doctor_search_skips_when_doctors_already_listed():
    from agent import nodes

    state = {
        "platform_mode": True,
        "available_doctors": [{"id": 5, "name": "Rahim"}],
        "messages": [HumanMessage(content="ডাক্তার দেখান")],
    }
    out = nodes._force_doctor_search(_toolless_reply(), state)
    assert not out.tool_calls


def test_force_doctor_search_skips_after_doctor_chosen():
    from agent import nodes

    state = {
        "platform_mode": True,
        "doctor_id": 5,
        "messages": [HumanMessage(content="ডাক্তারের ফি কত?")],
    }
    out = nodes._force_doctor_search(_toolless_reply(), state)
    assert not out.tool_calls


def test_force_doctor_search_skips_non_doctor_smalltalk():
    from agent import nodes

    state = {
        "platform_mode": True,
        "messages": [HumanMessage(content="আসসালামু আলাইকুম")],
    }
    out = nodes._force_doctor_search(_toolless_reply(), state)
    assert not out.tool_calls


def test_force_doctor_search_first_hop_only():
    # Last message is a tool result (mid-ReAct) → guard must stay out of it.
    from agent import nodes
    from langchain_core.messages import ToolMessage

    state = {
        "platform_mode": True,
        "messages": [
            HumanMessage(content="কার্ডিওলজিস্ট দরকার"),
            ToolMessage(content="NO_DOCTORS_FOUND", tool_call_id="t1"),
        ],
    }
    out = nodes._force_doctor_search(_toolless_reply(), state)
    assert not out.tool_calls


# ---------------------------------------------------------------------------
# _dedupe_tool_calls — parallel same-key update hardening
# ---------------------------------------------------------------------------

def test_dedupe_tool_calls_drops_exact_duplicates():
    from agent import nodes

    msg = AIMessage(content="", tool_calls=[
        {"name": "search_doctors", "args": {"query": "cardio"}, "id": "a", "type": "tool_call"},
        {"name": "search_doctors", "args": {"query": "cardio"}, "id": "b", "type": "tool_call"},
        {"name": "search_doctors", "args": {"query": "derm"}, "id": "c", "type": "tool_call"},
    ])
    out = nodes._dedupe_tool_calls(msg)
    assert [tc["id"] for tc in out.tool_calls] == ["a", "c"]


def test_dedupe_tool_calls_keeps_single_call():
    from agent import nodes

    msg = AIMessage(content="", tool_calls=[
        {"name": "list_doctors", "args": {}, "id": "a", "type": "tool_call"},
    ])
    assert len(nodes._dedupe_tool_calls(msg).tool_calls) == 1


# ---------------------------------------------------------------------------
# No-slots alternatives — get_available_slots offers same-specialty doctors
# ---------------------------------------------------------------------------

ALT_ROW = {
    "id": 9, "clinic_id": 4, "hospital_id": 2,
    "name": "Karim", "degrees": "MBBS", "specialty": "Cardiology",
    "description": "", "has_photo": False,
    "hospital_name": "Metro Hospital", "department_name": "Cardiology",
    "fee_new": 600, "fee_followup": None,
    "avg_rating": 4.0, "review_count": 3,
}


def _slots_by_doctor(mapping):
    """_get_available_slots stub keyed by doctor_id (None = clinic default)."""
    async def fake(clinic_id, days_ahead=None, limit=None, doctor_id=None):
        return list(mapping.get(doctor_id, []))
    return fake


async def test_no_slots_platform_offers_alternatives(monkeypatch):
    from agent import tools

    # Chosen doctor (5) has nothing; alternative (9) has a slot.
    monkeypatch.setattr(
        tools, "_get_available_slots",
        _slots_by_doctor({5: [], 9: [dict(NEXT_SLOT)]}),
    )
    monkeypatch.setattr(
        tools, "_get_doctor",
        AsyncMock(return_value={"id": 5, "specialty": "Cardiology"}),
    )
    # Search returns the stuck doctor too — it must be excluded.
    monkeypatch.setattr(
        tools, "_search_doctors_platform",
        AsyncMock(return_value=[dict(SEARCH_ROW), dict(ALT_ROW)]),
    )

    cmd = await tools.get_available_slots.coroutine(
        tool_call_id="t1",
        state={"platform_mode": True, "clinic_id": 2, "doctor_id": 5},
    )
    text = cmd.update["messages"][0].content
    assert text.startswith("NO_SLOTS_AVAILABLE")
    assert "ALTERNATIVE_DOCTORS" in text
    assert "Karim" in text and "Metro Hospital" in text
    assert "Rahim" not in text  # the stuck doctor is not its own alternative
    # choose_doctor(n) must work directly on the alternatives.
    assert cmd.update["available_doctors"] == [{
        "id": 9, "name": "Karim", "clinic_id": 4, "hospital_id": 2,
        "hospital_name": "Metro Hospital",
    }]
    assert cmd.update["slots_shown"] is False


async def test_no_slots_department_mode_unchanged(monkeypatch):
    from agent import tools

    monkeypatch.setattr(tools, "_get_available_slots", _slots_by_doctor({}))
    search = AsyncMock()
    monkeypatch.setattr(tools, "_search_doctors_platform", search)

    cmd = await tools.get_available_slots.coroutine(
        tool_call_id="t1", state={"clinic_id": 2, "doctor_id": 5},
    )
    assert cmd.update["messages"][0].content == "NO_SLOTS_AVAILABLE"
    assert "available_doctors" not in cmd.update
    search.assert_not_awaited()


async def test_no_slots_alternatives_failure_degrades(monkeypatch):
    from agent import tools

    monkeypatch.setattr(tools, "_get_available_slots", _slots_by_doctor({}))
    monkeypatch.setattr(
        tools, "_get_doctor", AsyncMock(side_effect=RuntimeError("db down"))
    )
    cmd = await tools.get_available_slots.coroutine(
        tool_call_id="t1",
        state={"platform_mode": True, "clinic_id": 2, "doctor_id": 5},
    )
    assert cmd.update["messages"][0].content == "NO_SLOTS_AVAILABLE"
    assert "available_doctors" not in cmd.update


async def test_no_slots_alternatives_all_booked_degrades(monkeypatch):
    from agent import tools

    # Alternatives exist but none has an upcoming slot → plain marker.
    monkeypatch.setattr(tools, "_get_available_slots", _slots_by_doctor({}))
    monkeypatch.setattr(
        tools, "_get_doctor",
        AsyncMock(return_value={"id": 5, "specialty": "Cardiology"}),
    )
    monkeypatch.setattr(
        tools, "_search_doctors_platform",
        AsyncMock(return_value=[dict(ALT_ROW)]),
    )
    cmd = await tools.get_available_slots.coroutine(
        tool_call_id="t1",
        state={"platform_mode": True, "clinic_id": 2, "doctor_id": 5},
    )
    assert cmd.update["messages"][0].content == "NO_SLOTS_AVAILABLE"


async def test_prompt_context_platform_mentions_alternatives_flow():
    from agent import nodes

    ctx = await nodes._prompt_context({"platform_mode": True}, {})
    assert "ALTERNATIVE_DOCTORS" in ctx["ivr_context"]
    assert "choose_doctor" in ctx["ivr_context"]


async def test_prompt_context_platform_pins_doctor_branding(monkeypatch):
    """Landing on a clinic must not swap the {doctor} branding placeholder —
    a changed head means a full CPU re-prefill on every later turn."""
    from agent import nodes
    from config import settings

    monkeypatch.setattr(nodes, "list_departments", AsyncMock(return_value=[]))
    before = await nodes._prompt_context({"platform_mode": True}, {})
    after = await nodes._prompt_context(
        {"platform_mode": True, "clinic_id": 2, "hospital_id": 1},
        {"name": "কার্ডিওলজি", "doctor_name": "ডা. রহিম"},
    )
    # Pinned to a stable GENERIC label (not settings.doctor_name, which is a
    # single-clinic placeholder like "Dr. Smith" that would leak into the
    # marketplace greeting). Stability is what keeps the KV-cache head intact.
    assert before["doctor"] == after["doctor"]
    assert before["doctor"] == "our specialist doctors across many hospitals"
    assert before["doctor"] != settings.doctor_name


# ---------------------------------------------------------------------------
# search_hospital_info tool — cross-hospital fallback + RAG prompt stability
# ---------------------------------------------------------------------------

async def test_search_hospital_info_cross_search_when_no_hospital_in_state(monkeypatch):
    from agent import tools

    search = AsyncMock(return_value=["[City Hospital — উৎস: policy.pdf] সকাল ৯টা"])
    monkeypatch.setattr(tools, "_search_docs", search)
    cmd = await tools.search_hospital_info.coroutine(
        query="ভিজিটিং আওয়ার কি?", tool_call_id="t1", state={"platform_mode": True},
    )
    search.assert_awaited_once_with(None, "ভিজিটিং আওয়ার কি?", k=4)
    text = cmd.update["messages"][0].content
    assert text.startswith("HOSPITAL_INFO:")
    assert "City Hospital" in text


async def test_search_hospital_info_scoped_once_hospital_chosen(monkeypatch):
    from agent import tools

    search = AsyncMock(return_value=["[উৎস: policy.pdf] সকাল ৯টা"])
    monkeypatch.setattr(tools, "_search_docs", search)
    cmd = await tools.search_hospital_info.coroutine(
        query="ভিজিটিং আওয়ার কি?", tool_call_id="t1",
        state={"platform_mode": True, "hospital_id": 3},
    )
    search.assert_awaited_once_with(3, "ভিজিটিং আওয়ার কি?", k=4)
    assert "HOSPITAL_INFO:" in cmd.update["messages"][0].content


async def test_search_hospital_info_no_results_returns_no_info(monkeypatch):
    from agent import tools

    monkeypatch.setattr(tools, "_search_docs", AsyncMock(return_value=[]))
    cmd = await tools.search_hospital_info.coroutine(
        query="যা কিছু", tool_call_id="t1", state={"platform_mode": True},
    )
    assert "NO_INFO" in cmd.update["messages"][0].content


async def test_prompt_context_platform_rag_context_static_before_and_after_choose_doctor(monkeypatch):
    """The rag_context section must not change when choose_doctor lands the
    platform thread on a hospital — a mid-thread change would invalidate the
    Ollama prompt-prefix KV cache from that point on."""
    from agent import nodes

    monkeypatch.setattr(nodes, "list_departments", AsyncMock(return_value=[]))
    monkeypatch.setitem(nodes._has_docs_cache, 1, True)  # even a docs-rich hospital
    before = await nodes._prompt_context({"platform_mode": True}, {})
    after = await nodes._prompt_context(
        {"platform_mode": True, "clinic_id": 2, "hospital_id": 1}, {}
    )
    assert before["rag_context"] == after["rag_context"] == nodes._PLATFORM_RAG_CONTEXT


async def test_prompt_context_non_platform_rag_context_still_gated_on_docs(monkeypatch):
    """Non-platform mode keeps the has-docs gate (unchanged by the platform fix)."""
    from agent import nodes

    monkeypatch.setitem(nodes._has_docs_cache, 5, False)
    ctx = await nodes._prompt_context({"hospital_id": 5}, {})
    assert ctx["rag_context"] == ""

    monkeypatch.setitem(nodes._has_docs_cache, 5, True)
    ctx = await nodes._prompt_context({"hospital_id": 5}, {})
    assert "search_hospital_info" in ctx["rag_context"]
    assert ctx["rag_context"] != nodes._PLATFORM_RAG_CONTEXT
