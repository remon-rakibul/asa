"""Tests for agent/router.py — should_continue routes on tool_calls presence."""

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END

from agent.router import should_continue


def test_should_continue_routes_to_tools_when_tool_call_present():
    state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[{"name": "get_available_slots", "args": {}, "id": "call-1"}],
            )
        ]
    }
    assert should_continue(state) == "tools"


def test_should_continue_ends_when_no_tool_calls():
    state = {"messages": [AIMessage(content="আপনার নাম কী?")]}
    assert should_continue(state) == END


def test_should_continue_ends_on_empty_tool_calls():
    state = {"messages": [AIMessage(content="ধন্যবাদ।", tool_calls=[])]}
    assert should_continue(state) == END


def test_force_slot_lookup_injects_tool_call():
    """'দেখছি…' announcements without a tool call get get_available_slots forced."""
    from langchain_core.messages import AIMessage
    from agent.nodes import _force_slot_lookup

    state = {"clinic_id": 1, "messages": [HumanMessage(content="হ্যাঁ সঠিক")]}
    msg = AIMessage(content="এখন আমি আপনার জন্য উপলব্ধ স্লটগুলো দেখছি।")
    out = _force_slot_lookup(msg, state)
    assert out.tool_calls and out.tool_calls[0]["name"] == "get_available_slots"

    # Already fetched slots → leave the reply alone.
    msg2 = AIMessage(content="আমি স্লটগুলো দেখছি।")
    assert not _force_slot_lookup(msg2, {"clinic_id": 1, "slots_shown": True}).tool_calls

    # Unrelated text → untouched.
    msg3 = AIMessage(content="আপনার বয়স কত?")
    assert not _force_slot_lookup(msg3, state).tool_calls

    # Real tool call present → untouched.
    msg4 = AIMessage(content="", tool_calls=[
        {"name": "book_appointment", "args": {}, "id": "x", "type": "tool_call"}])
    assert _force_slot_lookup(msg4, state).tool_calls[0]["name"] == "book_appointment"

    # Live-observed 2026-07-08: the model dodged every slot keyword with
    # "সময়গুলো … দেখাচ্ছি" and the turn ended on a dead promise.
    msg5 = AIMessage(content="ডা. Doctor-এর জন্য এখন যে সময়গুলো পাওয়া যাচ্ছে, "
                             "সেগুলো আমি আপনাকে দেখাচ্ছি।")
    out5 = _force_slot_lookup(msg5, state)
    assert out5.tool_calls and out5.tool_calls[0]["name"] == "get_available_slots"


def test_force_slot_lookup_fires_when_patient_request_is_dodged():
    """Live-observed 2026-07-08 (two turns in a row): the patient explicitly
    asked for slots and the model REFUSED with ever-new wording instead of
    calling the tool. The guard keys on the patient's stable request, not the
    model's dodge phrasing — and a re-ask forces a fresh fetch even after
    slots_shown."""
    from langchain_core.messages import AIMessage
    from agent.nodes import _force_slot_lookup

    dodges = [
        ("আগামীকালের ফাঁকা স্লটগুলো দেখান",
         "আমি দুঃখিত, আমি শুধু আজকের বা নিকটতম সময়ের স্লটগুলো দেখতে পারি। "
         "আপনি কি অন্য কোনো দিনের জন্য জানতে চাইছেন?"),
        ("হ্যাঁ, আগামীকালের স্লট দেখান",
         "আমি দুঃখিত, আমার কাছে শুধুমাত্র নির্দিষ্ট তারিখের স্লট দেখার ব্যবস্থা নেই। "
         "আপনি কি চান আমি আবার সব উপলব্ধ স্লটগুলো দেখাই?"),
    ]
    for ask, dodge in dodges:
        state = {"clinic_id": 2, "slots_shown": True,
                 "messages": [HumanMessage(content=ask)]}
        out = _force_slot_lookup(AIMessage(content=dodge), state)
        assert out.tool_calls and out.tool_calls[0]["name"] == "get_available_slots"
        assert out.content  # dodge text already streamed; history keeps it


def test_force_slot_lookup_slots_shown_still_gates_non_requests():
    """slots_shown still suppresses the guard when the patient did NOT ask for
    slots — e.g. declining a shown time must not re-trigger a fetch."""
    from langchain_core.messages import AIMessage
    from agent.nodes import _force_slot_lookup

    state = {"clinic_id": 2, "slots_shown": True,
             "messages": [HumanMessage(content="ওই সময় হবে না")]}
    out = _force_slot_lookup(AIMessage(content="কোন সময় সুবিধা হবে? আমি সময়গুলো দেখাচ্ছি।"), state)
    assert not out.tool_calls


def test_route_after_tools_farewell_only_on_fresh_booking():
    """post_booking fires on a BOOKED tool result — not merely because the
    thread has an appointment_id from an earlier booking."""
    from langchain_core.messages import ToolMessage
    from agent.router import route_after_tools

    booked = {"messages": [ToolMessage(content="BOOKED: appointment_id=a1, serial_number=3",
                                       tool_call_id="t1")],
              "appointment_id": "a1"}
    assert route_after_tools(booked) == "post_booking"

    # Same thread later: listing appointments must NOT re-trigger the farewell.
    listing = {"messages": [ToolMessage(content="MY_APPOINTMENTS:\n1. কার্ডিওলজি",
                                        tool_call_id="t2")],
               "appointment_id": "a1"}
    assert route_after_tools(listing) == "call_model"

    # Failed booking → back to the model for recovery.
    taken = {"messages": [ToolMessage(content="SLOT_TAKEN: ...", tool_call_id="t3")],
             "appointment_id": None}
    assert route_after_tools(taken) == "call_model"


def test_affirmative_token_matching():
    from agent.runner import _affirmative

    assert _affirmative("হ্যাঁ") is True
    assert _affirmative("জি, নিশ্চিত করুন") is True
    assert _affirmative("ঠিক আছে") is True
    assert _affirmative("yes") is True
    # Negation always wins, even mixed with yes-words.
    assert _affirmative("না") is False
    assert _affirmative("না, থাক") is False
    assert _affirmative("হ্যাঁ না") is False
    # Substring traps: "হাসপাতাল" contains "হা"; numbers contain "1".
    assert _affirmative("হাসপাতালে যাবো") is False
    assert _affirmative("01711111111") is False
    assert _affirmative("") is False


def test_force_slot_lookup_catches_fabricated_slot_list():
    from langchain_core.messages import AIMessage

    from agent.nodes import _force_slot_lookup

    fake = AIMessage(content="১. [datetime=২০২৪-০৭-১০T১০:০০:০০+০৬:০০] সকাল ১০টা কোনটি নেবেন?")
    state = {"clinic_id": 2, "slots_shown": None, "messages": [HumanMessage(content="স্লট দেখান")]}
    out = _force_slot_lookup(fake, state)
    assert out.tool_calls and out.tool_calls[0]["name"] == "get_available_slots"
    assert "datetime=" not in out.content  # invented list replaced


def test_force_slot_lookup_leaves_real_readout_alone():
    from langchain_core.messages import AIMessage

    from agent.nodes import _force_slot_lookup

    msg = AIMessage(content="১. সকাল ১০টা [datetime=2026-07-05T10:00:00+06:00]")
    state = {"clinic_id": 2, "slots_shown": True}  # real slots were fetched
    out = _force_slot_lookup(msg, state)
    assert not out.tool_calls


def test_sanitize_strips_datetime_markers():
    from utils.text import sanitize_text

    assert sanitize_text("সকাল ১০টা [datetime=2026-07-05T10:00:00+06:00] নিন") == "সকাল ১০টা নিন"
    assert "datetime" not in sanitize_text("১. [datetime=২০২৪-০৭-১০T১০:০০:০০+০৬:০০] সকাল ১০টা")


def test_force_slot_lookup_catches_dated_fabrication_without_marker():
    """Live-observed variant: invented slot list with plain dates (Bangla
    digits, no [datetime=] marker) — e.g. 'তারিখ: ২০২৪-০৭-১০, সময়: সকাল ১০টা'."""
    from langchain_core.messages import AIMessage

    from agent.nodes import _force_slot_lookup

    fake = AIMessage(content="১. তারিখ: ২০২৪-০৭-১০, সময়: সকাল ১০টা ২. তারিখ: ২০২৪-০৭-১১, সময়: দুপুর ১২টা")
    state = {"clinic_id": 2, "slots_shown": None, "messages": [HumanMessage(content="স্লট দেখান")]}
    out = _force_slot_lookup(fake, state)
    assert out.tool_calls and out.tool_calls[0]["name"] == "get_available_slots"
    assert "২০২৪" not in out.content  # invented list replaced, not streamed


def test_force_slot_lookup_ascii_date_also_caught():
    from langchain_core.messages import AIMessage

    from agent.nodes import _force_slot_lookup

    fake = AIMessage(content="আপনার জন্য 2024-07-10 তারিখে স্লট আছে।")
    state = {"clinic_id": 2, "slots_shown": None, "messages": [HumanMessage(content="স্লট দেখান")]}
    out = _force_slot_lookup(fake, state)
    assert out.tool_calls


def test_force_slot_lookup_date_after_slots_shown_untouched():
    from langchain_core.messages import AIMessage

    from agent.nodes import _force_slot_lookup

    msg = AIMessage(content="আপনার অ্যাপয়েন্টমেন্ট 2026-07-05 তারিখে নিশ্চিত হয়েছে।")
    state = {"clinic_id": 2, "slots_shown": True}
    out = _force_slot_lookup(msg, state)
    assert not out.tool_calls


def test_force_doctor_lookup_catches_fabricated_doctor_list():
    """The exact live incident: a numbered doctor list with invented names,
    produced with no list_doctors call — real clinic had entirely different
    doctors, and the patient 'chose' one that does not exist."""
    from langchain_core.messages import AIMessage

    from agent.nodes import _force_doctor_lookup

    fake = AIMessage(content=(
        "আপনার জন্য উপলব্ধ ডাক্তারদের তালিকা নিচে দেওয়া হলো:\n\n"
        "১. ডাক্তার রহমান (সাধারণ মেডিসিন)\n"
        "২. ডাক্তার আক্তার (শিশু বিশেষজ্ঞ)\n"
        "৩. ডাক্তার হাসান (অর্থোপেডিক)\n\n"
        "আপনি কোন ডাক্তারকে দেখাতে চান?"
    ))
    state = {"clinic_id": 2, "messages": [HumanMessage(content="yes")]}
    out = _force_doctor_lookup(fake, state)
    assert out.tool_calls and out.tool_calls[0]["name"] == "list_doctors"
    assert out.content == ""  # invented names never shown


def test_force_doctor_lookup_ascii_list_also_caught():
    from langchain_core.messages import AIMessage

    from agent.nodes import _force_doctor_lookup

    fake = AIMessage(content="1. Dr. Rahman (Medicine)\n2. Dr. Akter (Paediatrics)")
    state = {"clinic_id": 1, "messages": [HumanMessage(content="show doctors")]}
    assert _force_doctor_lookup(fake, state).tool_calls


def test_force_doctor_lookup_skips_when_real_list_already_fetched():
    from langchain_core.messages import AIMessage

    from agent.nodes import _force_doctor_lookup

    msg = AIMessage(content="১. ডাক্তার Doctor\n২. ডাক্তার New doctor")
    state = {
        "clinic_id": 2,
        "available_doctors": [{"id": 1, "name": "Doctor"}],
        "messages": [HumanMessage(content="আবার দেখান")],
    }
    assert not _force_doctor_lookup(msg, state).tool_calls


async def _run_call_model(monkeypatch, reply):
    """Drive call_model_node with a stubbed context/LLM returning `reply`."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import agent.nodes as nodes

    monkeypatch.setattr(nodes, "_clinic_cfg", AsyncMock(return_value={}))
    monkeypatch.setattr(nodes, "_prompt_context", AsyncMock(return_value={}))
    monkeypatch.setattr(nodes.prompts, "SYSTEM_PROMPT", "SYS")
    llm = SimpleNamespace(ainvoke=AsyncMock(return_value=reply))
    monkeypatch.setattr(nodes, "_llm_for", lambda hospital, account, platform=False: llm)

    state = {"clinic_id": 2, "messages": [HumanMessage(content="স্লট দেখান")]}
    update = await nodes.call_model_node(state)
    return update["messages"][-1]


async def test_call_model_strips_fabricated_listing_from_tool_call_response(monkeypatch):
    """Live-observed 2026-07-08: one response carried BOTH a get_available_slots
    call and an invented numbered slot list. The listing must never persist in
    thread history — the node blanks the content while keeping the tool call
    (the runner separately retracts what already streamed)."""
    from langchain_core.messages import AIMessage

    reply = AIMessage(
        content="১. সকাল ৯টা ২. সকাল ১০টা — কোনটি নেবেন?",
        tool_calls=[{"name": "get_available_slots", "args": {}, "id": "t1",
                     "type": "tool_call"}],
    )
    out = await _run_call_model(monkeypatch, reply)
    assert out.tool_calls and out.tool_calls[0]["name"] == "get_available_slots"
    assert out.content == ""


async def test_call_model_keeps_plain_announce_on_tool_call_response(monkeypatch):
    """Announce text without listing data ('দেখছি…') alongside a tool call is
    legitimate speech and must survive."""
    from langchain_core.messages import AIMessage

    reply = AIMessage(
        content="আমি এখন সময়গুলো দেখছি।",
        tool_calls=[{"name": "get_available_slots", "args": {}, "id": "t1",
                     "type": "tool_call"}],
    )
    out = await _run_call_model(monkeypatch, reply)
    assert out.tool_calls
    assert out.content == "আমি এখন সময়গুলো দেখছি।"


def test_force_doctor_lookup_first_hop_only_and_plain_text_untouched():
    from langchain_core.messages import AIMessage, ToolMessage

    from agent.nodes import _force_doctor_lookup

    # Post-tool hop (last message is a tool result) → guard must not fire.
    fake = AIMessage(content="১. ডাক্তার রহমান")
    post_tool = {"clinic_id": 2, "messages": [ToolMessage(content="x", tool_call_id="t")]}
    assert not _force_doctor_lookup(fake, post_tool).tool_calls

    # Mentioning a doctor without a numbered list (pre-selected mode) → untouched.
    ok = AIMessage(content="আমি আপনাকে ডা. Doctor-এর অ্যাপয়েন্টমেন্ট নিতে সাহায্য করছি।")
    state = {"clinic_id": 2, "messages": [HumanMessage(content="hi")]}
    out = _force_doctor_lookup(ok, state)
    assert not out.tool_calls and out.content
