"""call_model node for the appointment-setter ReAct graph.

One node. The LLM decides each turn whether to respond to the patient
or call a tool. Tool execution is handled by ToolNode in graph.py.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from functools import lru_cache

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
)
from langgraph.config import get_store, get_stream_writer

from config import settings
from tools.database import get_clinic, get_doctor, get_doctors_for_clinic, get_hospital, list_departments
from utils.text import looks_fabricated_listing

from . import prompts
from .state import AppointmentState
from .tools import BOOKING_TOOLS, MANAGE_TOOLS, search_hospital_info

log = logging.getLogger(__name__)

_CACHE_TTL_S = 300
_CACHE_MAX = 1000
_clinic_cache: dict[int, tuple[dict, float]] = {}

_llm_semaphore: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    global _llm_semaphore
    if _llm_semaphore is None:
        _llm_semaphore = asyncio.Semaphore(settings.ollama_max_concurrent)
    return _llm_semaphore


def invalidate_clinic(clinic_id: int) -> None:
    """Drop a clinic's cached config so the next turn re-reads it from the DB."""
    _clinic_cache.pop(clinic_id, None)


async def _clinic_cfg(state: AppointmentState) -> dict:
    """TTL-cached config row for this conversation's clinic ({} if unscoped)."""
    cid = state.get("clinic_id")
    if cid is None:
        return {}
    entry = _clinic_cache.get(cid)
    if entry is not None:
        cfg, expires_at = entry
        if time.monotonic() < expires_at:
            return cfg
    if len(_clinic_cache) >= _CACHE_MAX:
        oldest = min(_clinic_cache, key=lambda k: _clinic_cache[k][1])
        _clinic_cache.pop(oldest, None)
    cfg = await get_clinic(cid) or {}
    _clinic_cache[cid] = (cfg, time.monotonic() + _CACHE_TTL_S)
    return cfg


def _branding_of(cfg: dict) -> dict:
    return {
        "clinic": cfg.get("name") or settings.clinic_name,
        "doctor": cfg.get("doctor_name") or settings.doctor_name,
    }


def _last_human_message(state: AppointmentState) -> str | None:
    """Most recent patient message text, or None. Used both for semantic
    visit recall and as the query when forcing a missed RAG tool call."""
    for msg in reversed(state.get("messages") or []):
        if getattr(msg, "type", None) == "human" and getattr(msg, "content", None):
            return msg.content if isinstance(msg.content, str) else None
    return None


def _build_chat_model():
    """Build the chat model for the configured provider (LLM_PROVIDER)."""
    provider = (settings.llm_provider or "ollama").lower()

    if provider == "gemini":
        if not settings.gemini_api_key:
            log.warning(
                "LLM_PROVIDER=gemini but GEMINI_API_KEY is not set; using local Ollama"
            )
        else:
            from langchain_google_genai import ChatGoogleGenerativeAI

            return ChatGoogleGenerativeAI(
                model=settings.gemini_llm_model,
                google_api_key=settings.gemini_api_key,
                temperature=settings.gemini_temperature,
                streaming=True,
            )

    from langchain_ollama import ChatOllama

    kwargs = {}
    if settings.ollama_num_thread > 0:
        kwargs["num_thread"] = settings.ollama_num_thread
    return ChatOllama(
        model=settings.ollama_model,
        base_url=settings.ollama_base_url,
        temperature=settings.ollama_temperature,
        streaming=True,
        reasoning=False,      # disables thinking tokens (think:false in Ollama API)
        keep_alive=-1,        # keep model loaded in RAM indefinitely
        num_predict=512,      # cap output tokens so a runaway response can't block
        num_ctx=settings.ollama_num_ctx,
        **kwargs,
    )


@lru_cache
def _llm_bound(rag: bool, manage: bool):
    """LLM with a tool schema sized to the session.

    The small local model degrades with a bloated schema, so extra tools are
    bound only when usable: RAG when the hospital has documents, manage tools
    when the patient is authenticated.
    """
    tools = list(BOOKING_TOOLS)
    if manage:
        tools += MANAGE_TOOLS
    if rag:
        tools.append(search_hospital_info)
    return _build_chat_model().bind_tools(tools)


@lru_cache
def _llm_prewarm(rag: bool, manage: bool):
    """Same tool binding as a real turn, but generation capped to ONE token.

    Prewarm only needs the prefill; the tool schema must match the real turn
    exactly (tools are rendered into Ollama's prompt, so a different binding
    breaks the prefix-cache match). num_predict is set on the model instance —
    passing it per-call via .bind() reaches ollama's AsyncClient.chat() as an
    unknown kwarg and raises TypeError.
    """
    model = _build_chat_model()
    model.num_predict = 1
    tools = list(BOOKING_TOOLS)
    if manage:
        tools += MANAGE_TOOLS
    if rag:
        tools.append(search_hospital_info)
    return model.bind_tools(tools)


@lru_cache
def _llm_suggest(rag: bool, manage: bool):
    """Model for quick-reply chip composition, tool binding identical to the turn.

    Chips are generated by EXTENDING the just-finished turn's prompt (same
    system prompt, same tool schema, same thread history + one instruction),
    so Ollama's prompt-prefix KV cache is reused and only the short instruction
    is prefilled. A different binding (e.g. the tool-free _llm_plain) would
    render a different prompt head and pay a full cold prefill every turn.
    Decode is capped: chips are a few words each.
    """
    model = _build_chat_model()
    model.num_predict = 64
    tools = list(BOOKING_TOOLS)
    if manage:
        tools += MANAGE_TOOLS
    if rag:
        tools.append(search_hospital_info)
    return model.bind_tools(tools)


def _llm_booking_only():
    """LLM with only the 5 booking tools — smaller schema, faster inference."""
    return _llm_bound(rag=False, manage=False)


def _llm_for(hospital_id: int | None, account_id: int | None = None):
    """Return the right LLM binding for this session."""
    rag = bool(hospital_id and _hospital_has_docs_sync(hospital_id))
    return _llm_bound(rag=rag, manage=bool(account_id))


# Simple sync cache: {hospital_id: bool} so we don't await inside lru_cache.
_has_docs_cache: dict[int, bool] = {}


def _hospital_has_docs_sync(hospital_id: int) -> bool:
    """Return cached True if this hospital has any uploaded documents."""
    return _has_docs_cache.get(hospital_id, False)


async def _refresh_has_docs(hospital_id: int) -> None:
    from tools.database import hospital_has_documents
    _has_docs_cache[hospital_id] = await hospital_has_documents(hospital_id)


def _farewell(name: str, slot: str, serial: int | None) -> str:
    from utils.text import to_bangla_digits
    serial_line = f" আপনার সিরিয়াল নম্বর: {to_bangla_digits(serial)}।" if serial else ""
    return (
        f"ধন্যবাদ {name}! আপনার {slot} এর অ্যাপয়েন্টমেন্ট সফলভাবে বুক হয়েছে।"
        f"{serial_line}"
        " একটি নিশ্চিতকরণ বার্তা আপনার মোবাইলে পাঠানো হয়েছে। আল্লাহ হাফেজ।"
    )


async def post_booking_node(state: AppointmentState) -> dict:
    """Emit a deterministic farewell after a successful booking — no LLM call needed."""
    name = state.get("patient_name") or "আপনি"
    slot = state.get("slot_label") or "আপনার বেছে নেওয়া সময়ে"
    serial = state.get("serial_number")
    farewell = _farewell(name, slot, serial)

    # Persist patient memory for authenticated portal patients (best-effort).
    try:
        await _write_patient_memory(state)
    except Exception as exc:
        log.warning("patient memory write failed: %s", exc)

    return {"messages": [AIMessage(content=farewell)]}


async def _load_patient_context(
    account_id: int, query: str | None = None, store=None
) -> tuple[str, dict | None]:
    """Return (patient-context instruction string, most recent visit or None).

    Visits are recalled semantically (store index over their Bangla summary)
    when a query is given, so "গতবারের ডাক্তার" style references surface the
    right visit. Returns ("", None) when the store is unavailable (tests, or
    a call from OUTSIDE a graph run — get_store() raises there, e.g. the
    prewarm endpoint) or the patient has no stored profile yet. Callers
    outside a graph run can pass the graph's store explicitly to get the
    same recall a real turn sees (compose_suggestions needs this for an
    exact prompt-prefix match with the turn that just ran).
    """
    if store is None:
        try:
            store = get_store()
        except RuntimeError:
            return "", None
    if store is None:
        return "", None
    namespace = ("patient_memory", str(account_id))
    profile_item = await store.aget(namespace, "profile")

    # Visit history: newest first for "last visit"; semantic ranking for recall.
    last_visit: dict | None = None
    visit_lines: list[str] = []
    try:
        items = await store.asearch(namespace, limit=20)
        visits = sorted(
            (i for i in items if i.key.startswith("visit:")),
            key=lambda i: i.created_at, reverse=True,
        )
        if visits:
            last_visit = dict(visits[0].value)
        recalled = visits[:2]
        if query:
            ranked = await store.asearch(namespace, query=query, limit=3)
            ranked_visits = [i for i in ranked if i.key.startswith("visit:")]
            if ranked_visits:
                recalled = ranked_visits[:2]
        # Render in stable (newest-first) order regardless of semantic rank:
        # the prompt prefix must not vary turn-to-turn, or the Ollama prompt
        # cache is invalidated and every turn pays a full CPU prefill.
        recalled = sorted(recalled, key=lambda i: str(i.created_at), reverse=True)
        visit_lines = [
            v.value.get("summary") or v.value.get("slot_label") or ""
            for v in recalled
        ]
        visit_lines = [line for line in visit_lines if line]
    except Exception as exc:
        log.debug("visit recall failed for account %s: %s", account_id, exc)

    if profile_item is None:
        return "", last_visit

    profile = profile_item.value
    name = profile.get("name") or ""
    age = profile.get("age")
    phone = profile.get("phone") or ""

    if name and age and phone:
        context = (
            f"RETURNING PATIENT — all details already known: name={name}, age={age}, "
            f"phone={phone}. Do NOT ask for name, age, or mobile number. "
            "Greet them warmly by first name, then call get_available_slots immediately."
        )
    elif name and phone:
        context = (
            f"RETURNING PATIENT — name={name}, phone={phone} are known. "
            "Do NOT ask for name or mobile. Greet by name, ask for age (step 2), "
            "then proceed to get_available_slots."
        )
    elif name:
        context = (
            f"RETURNING PATIENT — name={name} is known. "
            "Do NOT ask for their name. Greet by name, then ask for age and mobile number."
        )
    else:
        context = ""

    if context and visit_lines:
        context += (
            " Previous visits: " + "; ".join(visit_lines) + ". "
            "If the patient refers to their previous doctor or department "
            "(e.g. 'গতবারের ডাক্তার'), use these — do not ask again."
        )
    return context, last_visit


async def _write_patient_memory(state: AppointmentState) -> None:
    """Persist patient profile + booking visit to the PostgresStore after a successful booking.

    No-op when the store is unavailable or the patient is not authenticated.
    """
    store = get_store()
    if store is None:
        return
    account_id = state.get("patient_account_id")
    if not account_id:
        return

    namespace = ("patient_memory", str(account_id))

    # Upsert profile — merge new values on top of any existing ones.
    profile_item = await store.aget(namespace, "profile")
    profile: dict = dict(profile_item.value) if profile_item else {}
    if state.get("patient_name"):
        profile["name"] = state["patient_name"]
    if state.get("patient_age"):
        profile["age"] = state["patient_age"]
    if state.get("patient_mobile"):
        profile["phone"] = state["patient_mobile"]
    if profile:
        await store.aput(namespace, "profile", profile)

    # Append visit record keyed by appointment_id. The Bangla summary is the
    # semantically indexed field (store index config in graph.py) so later
    # turns can recall "গতবারের ডাক্তার" style references.
    appointment_id = state.get("appointment_id")
    if appointment_id:
        cfg = await _clinic_cfg(state)
        department = cfg.get("name") or ""
        doctor = cfg.get("doctor_name") or ""
        parts = [p for p in (department, f"ডা. {doctor}" if doctor else "", state.get("slot_label") or "") if p]
        visit = {
            "clinic_id": state.get("clinic_id"),
            "doctor_id": state.get("doctor_id"),
            "slot_label": state.get("slot_label"),
            "serial_number": state.get("serial_number"),
            "department_name": department or None,
            "doctor_name": doctor or None,
            "summary": "ভিজিট: " + " — ".join(parts) if parts else None,
        }
        await store.aput(namespace, f"visit:{appointment_id}", visit)


async def _prompt_context(state: AppointmentState, cfg: dict, store=None) -> dict:
    """Build the full prompt context dict, including dynamic IVR and doctor sections."""
    ctx = _branding_of(cfg)

    # IVR context: list departments when in hospital-routing mode (no clinic yet)
    clinic_id = state.get("clinic_id")
    hospital_id = state.get("hospital_id")
    departments = state.get("departments")
    if hospital_id and not clinic_id:
        if not departments:
            departments = await list_departments(hospital_id)
        if departments:
            dept_lines = "\n".join(
                f"{i + 1}. {d['name']}" + (f" ({d['specialty_code']})" if d.get("specialty_code") else "")
                for i, d in enumerate(departments)
            )
            ctx["ivr_context"] = (
                "HOSPITAL MODE — before asking for name/age/mobile, you MUST route the "
                "patient to the right department.\n"
                f"Available departments:\n{dept_lines}\n"
                "Ask which department and call select_department(department_number) "
                "with the patient's choice. Do NOT proceed to booking until department is chosen."
            )
        else:
            ctx["ivr_context"] = ""
    else:
        ctx["ivr_context"] = ""

    # Doctor context: two mutually exclusive modes — ask which doctor (multi-doctor
    # clinic, none chosen yet), or acknowledge one already picked in the portal
    # wizard before this chat started (state["doctor_id"] pre-seeded on turn 1).
    doctor_context = ""
    selected_doctor_id = state.get("doctor_id")
    if clinic_id and not selected_doctor_id:
        doctors = await get_doctors_for_clinic(clinic_id)
        if len(doctors) > 1:
            doctor_context = (
                "MULTI-DOCTOR MODE — after confirming the patient's mobile number, "
                "call list_doctors to show available doctors. Wait for the patient's "
                "choice, then call choose_doctor(doctor_number). "
                "Only then call get_available_slots."
            )
    elif selected_doctor_id:
        doctor = await get_doctor(selected_doctor_id, clinic_id=clinic_id)
        if doctor and doctor.get("name"):
            hosp = await get_hospital(hospital_id) if hospital_id else None
            # Identity line from everything the roster knows: degrees, specialty,
            # department (= this clinic's name), hospital. Small and stable per
            # thread, so it lives in the prompt's cached static head.
            identity = f"ডা. {doctor['name']}"
            if doctor.get("degrees"):
                identity += f", {doctor['degrees']}"
            if doctor.get("specialty"):
                identity += f" ({doctor['specialty']})"
            place_parts = [p for p in (cfg.get("name"), (hosp or {}).get("name")) if p]
            where = f" — {', '.join(place_parts)}" if place_parts else ""
            profile = (doctor.get("description") or "").strip()
            if len(profile) > 300:
                profile = profile[:300].rsplit(" ", 1)[0] + "…"
            profile_section = (
                "\nDoctor profile (admin-written background — draw on it naturally "
                f"in your own words if the patient asks about the doctor): {profile}"
                if profile else ""
            )
            doctor_context = (
                f"PRE-SELECTED DOCTOR — the patient already chose {identity}{where} "
                "in the portal, before this chat even started. That choice "
                "IS their intent to book — treat it exactly like the patient having "
                "already said 'I want an appointment': do not wait for them to state "
                "it again, do not open with a generic 'how can I help' greeting. "
                "Your very first message in this conversation must open by "
                f"naturally acknowledging you'll help them book with ডা. {doctor['name']}"
                ", then move straight into step 1 (asking their name) — in your own "
                "words each time, combined with anything you already know about "
                "them from patient context below, never a fixed script. Do NOT ask "
                "which doctor they want and do NOT call list_doctors or "
                "choose_doctor; once you have name/age/mobile call get_available_slots."
                f"{profile_section}"
            )
        # Stale/deleted doctor_id (get_doctor returned None): doctor_context stays
        # "" — degrade silently rather than mention a doctor that no longer exists.
    ctx["doctor_context"] = doctor_context

    # Rolling summary of trimmed-away older messages (long-thread compaction).
    ctx["summary_context"] = _summary_section(state.get("conversation_summary"))

    # Greeting style: clinic-admin guidance for the LLM-generated greeting.
    greet = (cfg.get("greeting_instructions") or "").strip()
    ctx["greeting_context"] = (
        "Greeting style (set by the clinic admin — follow it whenever you greet or "
        "welcome the patient, while keeping replies short and in Bangla): " + greet
    ) if greet else ""

    # Patient memory: load stored profile + visit recall for authenticated
    # portal patients. The latest user message drives semantic visit recall.
    account_id = state.get("patient_account_id")
    if account_id:
        query = _last_human_message(state)
        patient_context, last_visit = await _load_patient_context(
            account_id, query, store=store
        )
        ctx["patient_context"] = patient_context
        ctx["_last_visit"] = last_visit
    else:
        ctx["patient_context"] = ""
        ctx["_last_visit"] = None

    # Manage-appointments instructions: only when the manage tools are bound
    # (authenticated patient), mirroring the conditional binding in _llm_for.
    if account_id:
        ctx["manage_context"] = (
            "Managing existing appointments (patient is logged in): if the patient "
            "wants to see, cancel, or move their appointments, first call "
            "list_my_appointments and read the numbered list aloud. To cancel, call "
            "cancel_appointment(appointment_number). To move, call get_available_slots "
            "first, then reschedule_appointment(appointment_number, slot_datetime, "
            "slot_label) with the ISO datetime copied EXACTLY from AVAILABLE_SLOTS. "
            "A confirmation question is shown to the patient automatically — never ask "
            "for confirmation yourself. Only say an appointment is cancelled/moved after "
            "the tool returns CANCELLED or RESCHEDULED. If it returns CANCEL_ABORTED or "
            "RESCHEDULE_ABORTED the patient declined — acknowledge and continue helping."
        )
    else:
        ctx["manage_context"] = ""

    # RAG context: only include search_hospital_info instructions when the
    # hospital has uploaded documents (avoids wasting tokens otherwise).
    if hospital_id and _hospital_has_docs_sync(hospital_id):
        ctx["rag_context"] = (
            "Information queries: If the patient asks about hospital policies, visiting hours, "
            "fees, directions, a specific PERSON or NAME ('কে?', 'who is …?'), or any factual "
            "question that is NOT about booking an appointment, you MUST call "
            "search_hospital_info(query) with their question — never answer from memory and "
            "never say you don't know without searching first. When it returns text, answer "
            "the patient's question DIRECTLY from that text, summarized in Bangla — do not "
            "deflect to booking. Only if it returns NO_INFO, politely say that information "
            "is not available and offer to help with booking an appointment instead."
        )
    else:
        ctx["rag_context"] = ""

    return ctx


async def build_system_prompt(state: AppointmentState, store=None) -> str:
    """The exact system prompt a turn with this state would use.

    Safe to call outside a graph run (patient-memory recall degrades to empty
    there — see _load_patient_context — unless the graph's store is passed
    explicitly). Used by the prewarm endpoint to heat Ollama's prompt-prefix
    KV cache before the patient's first message, and by compose_suggestions
    to extend the finished turn's prompt without a cache miss.
    """
    cfg = await _clinic_cfg(state)
    ctx = await _prompt_context(state, cfg, store=store)
    ctx.pop("_last_visit", None)
    return prompts.SYSTEM_PROMPT.format(**ctx)


async def prewarm_turn(state: AppointmentState) -> None:
    """Heat the KV cache with the same prompt the first real turn will send.

    Invokes the SAME tool-bound model a real turn uses (tool schemas are part
    of Ollama's rendered prompt, so the prefix only matches with identical
    binding), capped to a single decoded token. Never competes with a real
    turn: skips outright if the LLM semaphore is contended. Fire-and-forget —
    failures only log.
    """
    semaphore = _get_semaphore()
    if semaphore.locked():
        log.debug("prewarm skipped — LLM busy")
        return
    try:
        # Refresh the has-docs cache FIRST: rag_context and the bound tool
        # schema both depend on it, and a mismatch with the real turn makes
        # the whole prewarmed prefix useless (rag_context sits in the head).
        hospital_id = state.get("hospital_id")
        if hospital_id:
            await _refresh_has_docs(hospital_id)
        system = await build_system_prompt(state)
        rag = bool(hospital_id and _hospital_has_docs_sync(hospital_id))
        llm = _llm_prewarm(rag=rag, manage=bool(state.get("patient_account_id")))
        async with semaphore:
            await asyncio.wait_for(
                llm.ainvoke([SystemMessage(content=system), HumanMessage(content="")]),
                timeout=settings.ollama_timeout_seconds,
            )
        log.info("prewarm complete for clinic %s", state.get("clinic_id"))
    except Exception as exc:
        log.info("prewarm failed (%s): %s — first turn will just be cold", type(exc).__name__, exc)


# Internal instruction for quick-reply chip composition. Never shown to the
# patient and never persisted into the thread — it is appended to a COPY of
# the thread's messages for one out-of-band model call. The chips themselves
# are entirely LLM-written (per the no-hardcoded-patient-text rule).
_SUGGESTION_INSTRUCTION = (
    "INTERNAL UI REQUEST — the patient does not see this message and you are "
    "NOT talking to them now. Based on the conversation so far, write the "
    "quick-reply buttons the PATIENT is most likely to tap next: 1 to 3 lines, "
    "each a short Bangla phrase (5 words max) in the patient's own voice. "
    "If your last message offered a numbered choice (doctors, departments), "
    "the buttons must be exactly those choices. One phrase per line — no "
    "numbering, no quotes, no explanations, no tool calls. If no useful "
    "suggestion exists, write only: NONE"
)


def _parse_suggestion_lines(content: str) -> list[str]:
    items: list[str] = []
    for line in content.splitlines():
        line = re.sub(r"^[\s\-•*]*(?:[\d০-৯]+[.)])?\s*", "", line).strip().strip('"').strip()
        if not line or line.upper() == "NONE" or len(line) > 48:
            continue
        items.append(line)
    return items[:3]


async def compose_suggestions(state: AppointmentState, store=None) -> list[str]:
    """LLM-composed quick-reply chips for the turn that just finished.

    Called by the runner with the thread's checkpointed state (same thread_id
    the conversation runs under) after the reply has streamed. Rebuilds the
    turn's exact prompt and appends one internal instruction, so the call is
    a cheap cache extension of the turn itself. Any failure returns [] — no
    chips is always an acceptable outcome, canned chips are not.
    """
    try:
        messages = list(state.get("messages") or [])
        if not messages:
            return []
        hospital_id = state.get("hospital_id")
        if hospital_id:
            await _refresh_has_docs(hospital_id)
        system = await build_system_prompt(state, store=store)
        rag = bool(hospital_id and _hospital_has_docs_sync(hospital_id))
        llm = _llm_suggest(rag=rag, manage=bool(state.get("patient_account_id")))
        history = [SystemMessage(content=system)] + messages + [
            HumanMessage(content=_SUGGESTION_INSTRUCTION)
        ]
        async with _get_semaphore():
            response = await asyncio.wait_for(
                llm.ainvoke(history), timeout=settings.ollama_timeout_seconds
            )
        if getattr(response, "tool_calls", None):
            return []
        content = getattr(response, "content", "")
        if not isinstance(content, str):
            return []
        return _parse_suggestion_lines(content)
    except Exception as exc:
        log.info("suggestion composition skipped (%s): %s", type(exc).__name__, exc)
        return []


# Internal instruction for the last-resort recovery reply. Deliberately tiny:
# it runs right after a turn already failed (often an Ollama timeout on a huge
# prefill), so the prompt must prefill in seconds, not minutes — no system
# prompt, no history. The patient-facing words are still the model's own.
_ERROR_RECOVERY_INSTRUCTION = (
    "INTERNAL REQUEST — you are the Bangla-speaking assistant at a clinic's "
    "appointment desk. Your attempt to answer the patient just failed because "
    "of a temporary technical problem, so they received nothing. Write the "
    "short message you will send them now, in Bangla, in your own words: "
    "briefly apologize and ask them to try again. One or two sentences only — "
    "no English, no technical details, no explanations of this request."
)


async def compose_error_reply() -> str:
    """LLM-composed recovery line for a turn that died before speaking.

    Called by the runner from its failure path. Returns "" when even this
    minimal call fails (LLM unreachable) — the caller then ends the stream
    without an `end` event so the client shows its retry affordance instead
    of fabricated agent speech. Never raises, never returns canned text.
    """
    async def _compose():
        # Semaphore inside the timeout: if the LLM is wedged behind another
        # request, waiting for a slot must not stall the failure path too.
        async with _get_semaphore():
            return await _llm_plain().ainvoke(
                [HumanMessage(content=_ERROR_RECOVERY_INSTRUCTION)]
            )

    try:
        response = await asyncio.wait_for(
            _compose(), timeout=settings.recovery_reply_timeout_seconds
        )
        text = getattr(response, "content", "")
        return text.strip() if isinstance(text, str) else ""
    except Exception as exc:
        log.info("recovery reply compose failed (%s): %s", type(exc).__name__, exc)
        return ""


# "I'm checking the slots/schedule…" style announcements. The small local model
# sometimes SAYS this instead of calling get_available_slots (despite the
# prompt), which silently ends the turn with nothing fetched.
_SLOT_WORD_RE = re.compile(r"স্লট|সময়|শিডিউল|schedule|slot", re.IGNORECASE)
_CHECKING_WORD_RE = re.compile(r"দেখছি|খুঁজছি|দেখাচ্ছি|checking|looking", re.IGNORECASE)
# The PATIENT explicitly asking to see slots ("স্লট দেখান", "কি সময় আছে?").
# Matching the patient's request instead of the model's dodge wording — the
# model invents a new evasive phrasing every time ("দেখতে পারি না", "আমার কাছে
# ব্যবস্থা নেই"…), the patient's ask is stable (same lesson as _INFO_QUESTION_RE).
_SLOT_REQUEST_RE = re.compile(
    r"দেখান|দেখাও|দেখতে চাই|দেখা যাবে|জানতে চাই|জানান|বলুন|কি আছে|আছে কি|"
    r"show|available|check",
    re.IGNORECASE,
)
# ISO-style date (ASCII or Bangla digits) in a model reply. Real dates only
# ever come from tool results, so one appearing BEFORE slots were fetched is
# always a fabricated slot list (live-observed: "তারিখ: ২০২৪-০৭-১০, সময়:
# সকাল ১০টা…" — no "datetime=" marker, so the marker check alone missed it).
_ISO_DATE_RE = re.compile(r"(\d{4}|[০-৯]{4})-(\d{2}|[০-৯]{2})-(\d{2}|[০-৯]{2})")


def _force_slot_lookup(response, state: AppointmentState):
    """If the model announced, dodged or FABRICATED a slot lookup, inject the real one.

    Deterministic guard against three small-model failures: announcing a
    lookup without calling the tool ("দেখছি…"); refusing/deflecting when the
    PATIENT explicitly asked to see slots (live-observed: "আমার কাছে নির্দিষ্ট
    তারিখের স্লট দেখার ব্যবস্থা নেই" — false, the tool is bound); and inventing
    a slot list outright — detected by the internal "datetime=" marker or ANY
    ISO-style date in the reply, since real slot data only exists after the
    tool ran. The fabricated text is replaced (never shown), then the real
    get_available_slots runs and the next turn reads actual slots. An explicit
    patient re-ask forces a fresh fetch even after slots_shown — re-asking
    means they want current data.
    """
    if getattr(response, "tool_calls", None):
        return response
    if not state.get("clinic_id"):
        return response
    msgs = state.get("messages") or []
    if not msgs or getattr(msgs[-1], "type", None) != "human":
        # Only force on the first hop of a turn — after a tool already ran,
        # re-forcing can loop (corrective retry handles weak post-tool replies).
        return response
    content = getattr(response, "content", "")
    if not isinstance(content, str):
        return response
    last_human = getattr(msgs[-1], "content", "")
    patient_asked = isinstance(last_human, str) and bool(
        _SLOT_WORD_RE.search(last_human) and _SLOT_REQUEST_RE.search(last_human)
    )
    if state.get("slots_shown") and not patient_asked:
        return response
    fabricated = "datetime=" in content or bool(_ISO_DATE_RE.search(content))
    announced = _SLOT_WORD_RE.search(content) and _CHECKING_WORD_RE.search(content)
    if fabricated:
        # Suppress the invented list (never store/speak it) — the forced tool
        # call below fetches real slots and the NEXT model turn reads them out
        # in its own words; no hardcoded patient-facing text.
        response.content = ""
    if fabricated or announced or patient_asked:
        log.info(
            "model %s slots without tool call — forcing get_available_slots",
            "fabricated" if fabricated else
            ("announced" if announced else "dodged patient request for"),
        )
        response.tool_calls = [{
            "name": "get_available_slots",
            "args": {},
            "id": f"forced-{uuid.uuid4().hex[:8]}",
            "type": "tool_call",
        }]
    return response


# Same small-model failure as slot lookups, for hospital-info questions: the
# model dodges search_hospital_info with a vague "I can help with that"
# instead of actually calling it. Live-observed with two DIFFERENT evasive
# phrasings for the same question ("আমি তথ্য খুঁজে দিতে পারি", then later
# "আমি আপনাকে সাহায্য করতে পারি") — matching the model's wording is too
# fragile, so this keys on the PATIENT's question instead (the same info
# categories rag_context already tells the model to look up: hours, fees,
# address, policies), independent of how the model chose to dodge.
_INFO_QUESTION_RE = re.compile(
    r"ভিজিটিং|দর্শনের সময়|খোলা|বন্ধ থাকে|সময়সূচ|ফি\b|খরচ|মূল্য|ঠিকানা|"
    r"কোথায় অবস্থিত|পার্কিং|নিয়মকানুন|visiting|hours|address|location|"
    # who-is / what-is questions about people or things in the knowledge base
    r"(^|\s)(who|what)\s+(is|are)\b|(^|\s)কে(\s|\?|$)|কার সম্পর্কে|সম্পর্কে (বল|জান)",
    re.IGNORECASE,
)


def _force_rag_lookup(response, state: AppointmentState, hospital_id: int | None):
    """If the patient asked an info question but no tool was called, force it.

    Fires only on the FIRST hop of a turn (last message is the patient's): if
    a tool already ran this turn, forcing the same search again would loop —
    the corrective-retry path handles a weak post-tool reply instead.
    """
    if getattr(response, "tool_calls", None):
        return response
    msgs = state.get("messages") or []
    if not msgs or getattr(msgs[-1], "type", None) != "human":
        return response
    if not hospital_id or not _hospital_has_docs_sync(hospital_id):
        return response
    query = _last_human_message(state)
    if not query or not _INFO_QUESTION_RE.search(query):
        return response
    log.info("info question with no tool call — forcing search_hospital_info")
    response.tool_calls = [{
        "name": "search_hospital_info",
        "args": {"query": query},
        "id": f"forced-{uuid.uuid4().hex[:8]}",
        "type": "tool_call",
    }]
    return response


# A doctor list can only come from list_doctors — the model has no other source
# of doctor names (doctor_context names at most ONE pre-selected doctor, never
# a numbered list). So a numbered "১. ডাক্তার …" reply while available_doctors
# is unset is always invented names (live-observed: "১. ডাক্তার রহমান / ২.
# ডাক্তার আক্তার / ৩. ডাক্তার হাসান" for a clinic whose real doctors are named
# neither — the patient then chose a doctor that does not exist and was booked
# with someone else without knowing).
_DOCTOR_LIST_RE = re.compile(
    r"(?m)^\s*[\d০-৯]+[.)]\s*(?:ডাক্তার|ডাঃ|ডা\.|Dr\.?|Doctor)\s"
)


def _force_doctor_lookup(response, state: AppointmentState):
    """If the model printed a doctor list without list_doctors, fetch the real one.

    Same deterministic pattern as _force_slot_lookup: the fabricated text is
    suppressed (never shown/stored), the real tool call is forced, and the next
    model hop presents the actual doctors in its own words.
    """
    if getattr(response, "tool_calls", None):
        return response
    if state.get("available_doctors") or not state.get("clinic_id"):
        return response
    msgs = state.get("messages") or []
    if not msgs or getattr(msgs[-1], "type", None) != "human":
        # First hop of a turn only — same loop-avoidance rule as the other guards.
        return response
    content = getattr(response, "content", "")
    if not isinstance(content, str) or not _DOCTOR_LIST_RE.search(content):
        return response
    log.info("model fabricated a doctor list without tool call — forcing list_doctors")
    response.content = ""
    response.tool_calls = [{
        "name": "list_doctors",
        "args": {},
        "id": f"forced-{uuid.uuid4().hex[:8]}",
        "type": "tool_call",
    }]
    return response


# Fallback text when the model reads a tool result but produces neither a
# tool call nor any spoken text — e.g. after NO_SLOTS_AVAILABLE, seen live
# with gemma4 producing a blank reply instead of the prompted apology. A
# blank turn looks like the app froze; give the patient something honest.
def _tool_call_key(tc: dict) -> tuple:
    return (tc.get("name"), tuple(sorted((tc.get("args") or {}).items())))


def _last_tool_text(state: AppointmentState) -> str:
    for msg in reversed(state.get("messages") or []):
        if getattr(msg, "type", None) == "tool":
            c = getattr(msg, "content", "")
            return c if isinstance(c, str) else str(c)
    return ""


def _corrective_instruction(response, state: AppointmentState) -> str | None:
    """Detect a broken model response and return corrective guidance for a
    tool-free retry — the retry composes the actual patient-facing words, so
    nothing the patient sees is ever canned/hardcoded.

    Two live-observed gemma4 failure modes:
    * Repeating the SAME tool call with the SAME args right after its result
      arrived (spiralled to 60+ search_hospital_info calls in one turn).
      The repeat is stripped here; the retry must speak instead.
    * An empty reply after reading a tool result (e.g. NO_SLOTS_AVAILABLE) —
      looks like the app froze.
    """
    tool_calls = getattr(response, "tool_calls", None) or []
    msgs = state.get("messages") or []
    if tool_calls:
        if len(msgs) >= 2 and getattr(msgs[-1], "type", None) == "tool":
            prev_calls = getattr(msgs[-2], "tool_calls", None) or []
            if prev_calls and _tool_call_key(tool_calls[0]) == _tool_call_key(prev_calls[0]):
                log.warning(
                    "blocking repeated tool call %s with identical args — corrective retry",
                    tool_calls[0].get("name"),
                )
                response.tool_calls = []
                return (
                    "You just tried to call the same tool with the same arguments "
                    "again, but it ALREADY returned this result:\n---\n"
                    f"{_last_tool_text(state)}\n---\n"
                    "Do NOT call any tool. Answer the patient's last question "
                    "DIRECTLY from that text in Bangla — summarize what it says. "
                    "Do not deflect to other topics unless the text truly lacks "
                    "the answer."
                )
        return None
    content = getattr(response, "content", "")
    if isinstance(content, str) and content.strip():
        return None
    log.info("model returned empty response — corrective retry")
    tool_note = ""
    last_tool = _last_tool_text(state)
    if last_tool:
        tool_note = (
            f"The last tool returned this result:\n---\n{last_tool}\n---\n"
            "Answer the patient's last question DIRECTLY from that text — "
            "summarize what it says in Bangla. Do not deflect to other topics "
            "or offer unrelated help unless the text truly lacks the answer.\n"
        )
    return (
        "Your previous reply was empty — the patient is waiting. " + tool_note +
        "Reply to the patient now in Bangla, in two or three short natural "
        "sentences. Do NOT call any tool."
    )


def _history_for_retry(history: list) -> list:
    """Retry history for the tool-free corrective call: cut back to the last
    patient message. A conversation that ends in tool-call/tool-result
    messages when the model has NO tools bound hits a chat-template shape
    gemma cannot complete (live-observed: the retry itself came back empty).
    The corrective instruction already carries the tool result as plain text.
    """
    end = len(history)
    while end > 0 and getattr(history[end - 1], "type", None) != "human":
        end -= 1
    return history[:end] if end > 0 else history


async def _corrective_reply(instruction: str, system: str, history: list, response):
    """One tool-free LLM retry that writes the recovery text itself.

    The plain model has no tools bound, so it cannot re-enter the loop; its
    tokens stream to the patient like any normal reply. If even the retry
    comes back empty/fails, the exception propagates to the node's
    RetryPolicy — no canned patient-facing text anywhere.
    """
    corrective_system = system + "\n\nIMPORTANT CORRECTION: " + instruction
    retry = await asyncio.wait_for(
        _llm_plain().ainvoke(
            [SystemMessage(content=corrective_system)] + _history_for_retry(history)
        ),
        timeout=settings.ollama_timeout_seconds,
    )
    text = getattr(retry, "content", "")
    if not (isinstance(text, str) and text.strip()):
        raise RuntimeError("corrective retry also returned an empty response")
    response.content = text
    response.tool_calls = []
    return response


# --------------------------------------------------------------------------- #
# Long-thread compaction: stable thread ids mean chat/WhatsApp threads grow
# forever, and every past message is re-prefetched by the CPU model each turn.
# Above _SUMMARIZE_AFTER messages, everything but the last _KEEP_RECENT is
# folded into a rolling Bangla summary (state.conversation_summary) and removed
# from the checkpointed history.
# --------------------------------------------------------------------------- #

_SUMMARIZE_AFTER = 24
_KEEP_RECENT = 8

_SUMMARY_PROMPT = (
    "You maintain a running summary of a clinic appointment conversation. "
    "Write ONE compact Bangla paragraph (max 100 words) keeping every fact "
    "needed to continue the conversation: patient name, age and mobile if "
    "given; department and doctor; appointments booked, cancelled or moved "
    "(with date and time); and any unresolved request. No greetings, no "
    "markdown — output only the summary."
)


def _summary_section(summary: str | None) -> str:
    text = (summary or "").strip()
    if not text:
        return ""
    return (
        "Summary of the earlier part of this conversation (older messages were "
        "trimmed to save space; treat these facts as established, do not re-ask "
        "for them): " + text
    )


@lru_cache
def _llm_plain():
    """Tool-free model instance for internal calls (summarization)."""
    return _build_chat_model()


def _summary_cut_index(messages: list) -> int:
    """Where to cut: summarize messages[:cut], keep messages[cut:] verbatim.

    The cut advances to a HumanMessage boundary so a kept ToolMessage is never
    orphaned from the AIMessage that called it (invalid history for the model).
    Returns 0 or len(messages) when no safe cut exists (caller skips compaction).
    """
    cut = max(0, len(messages) - _KEEP_RECENT)
    while cut < len(messages) and not isinstance(messages[cut], HumanMessage):
        cut += 1
    return cut


def _transcript_for_summary(old: list) -> str:
    lines = []
    for m in old:
        role = getattr(m, "type", "")
        content = getattr(m, "content", None)
        if not isinstance(content, str) or not content:
            continue
        if role == "human":
            lines.append(f"রোগী: {content}")
        elif role == "ai" and not getattr(m, "tool_calls", None):
            lines.append(f"এজেন্ট: {content}")
        elif role == "tool":
            lines.append(f"[tool result] {content[:200]}")
    return "\n".join(lines)


async def _summarize_history(state: AppointmentState) -> dict | None:
    """Fold old messages into the rolling summary.

    Returns {"summary", "kept", "removals"} or None when there is nothing to
    compact. Caller must hold the LLM semaphore.
    """
    messages = list(state.get("messages") or [])
    if len(messages) < _SUMMARIZE_AFTER:
        return None
    cut = _summary_cut_index(messages)
    if cut <= 0 or cut >= len(messages):
        return None
    old = messages[:cut]
    transcript = _transcript_for_summary(old)
    if not transcript:
        return None
    prior = (state.get("conversation_summary") or "").strip()
    parts = []
    if prior:
        parts.append(f"Previous summary:\n{prior}")
    parts.append(f"Conversation to fold into the summary:\n{transcript}")
    response = await asyncio.wait_for(
        _llm_plain().ainvoke(
            [SystemMessage(content=_SUMMARY_PROMPT), HumanMessage(content="\n\n".join(parts))],
            # The "internal" tag tells the runner's messages-mode stream to
            # drop these tokens — without it the summary would stream to the
            # patient as if it were the reply.
            config={"tags": ["internal"]},
        ),
        timeout=settings.ollama_timeout_seconds,
    )
    summary = getattr(response, "content", None)
    if not isinstance(summary, str) or not summary.strip():
        return None
    return {
        "summary": summary.strip(),
        "kept": messages[cut:],
        "removals": [RemoveMessage(id=m.id) for m in old if getattr(m, "id", None)],
    }


async def call_model_node(state: AppointmentState) -> dict:
    """Call the LLM with the full message history and tools bound.

    The LLM decides whether to speak to the patient or call a tool.
    Tool calls route to ToolNode; spoken replies route to END.
    """
    cfg = await _clinic_cfg(state)
    ctx = await _prompt_context(state, cfg)
    last_visit = ctx.pop("_last_visit", None)
    history = list(state.get("messages") or [])

    hospital_id = state.get("hospital_id")
    if hospital_id:
        await _refresh_has_docs(hospital_id)
    llm = _llm_for(hospital_id, state.get("patient_account_id"))

    semaphore = _get_semaphore()
    if semaphore.locked():
        # All LLM slots busy — tell the patient honestly instead of a frozen
        # spinner. Writer only exists inside a streamed graph run.
        try:
            get_stream_writer()({"type": "status", "text": "এজেন্ট ব্যস্ত, একটু অপেক্ষা করুন…"})
        except Exception:
            pass

    async with semaphore:
        compaction = None
        if len(history) >= _SUMMARIZE_AFTER:
            try:
                get_stream_writer()({"type": "status", "text": "আগের কথাগুলো গুছিয়ে নিচ্ছি…"})
            except Exception:
                pass
            try:
                compaction = await _summarize_history(state)
            except Exception as exc:
                log.warning(
                    "history compaction failed (%s): %s — continuing with full history",
                    type(exc).__name__, exc,
                )
        if compaction:
            ctx["summary_context"] = _summary_section(compaction["summary"])
            history = compaction["kept"]
        system = prompts.SYSTEM_PROMPT.format(**ctx)
        try:
            response = await asyncio.wait_for(
                llm.ainvoke([SystemMessage(content=system)] + history),
                timeout=settings.ollama_timeout_seconds,
            )
        except Exception as exc:
            log.warning("LLM call failed (%s): %s", type(exc).__name__, exc)
            raise

    # Model emitted a tool call AND its own invented listing in the same
    # response (live-observed: a made-up slot list streamed right before the
    # real lookup returned nothing). Strip the content so the fabrication
    # never persists in the thread history; the runner retracts what already
    # streamed (see stream_turn_tokens).
    if getattr(response, "tool_calls", None) and isinstance(response.content, str) \
            and looks_fabricated_listing(response.content):
        log.warning("stripping fabricated listing from tool-call response: %.120s", response.content)
        response.content = ""

    response = _force_slot_lookup(response, state)
    response = _force_rag_lookup(response, state, hospital_id)
    response = _force_doctor_lookup(response, state)
    # Broken response (empty reply / repeated failing tool call)? One
    # tool-free LLM retry composes the recovery in its own words — the
    # patient never sees hardcoded text.
    instruction = _corrective_instruction(response, state)
    if instruction:
        async with semaphore:
            response = await _corrective_reply(instruction, system, history, response)

    update: dict = {"messages": (compaction["removals"] if compaction else []) + [response]}
    if compaction:
        update["conversation_summary"] = compaction["summary"]
    if last_visit is not None and not state.get("last_visit"):
        # Surface the most recent remembered visit so the UI can offer a
        # one-tap "book Dr. X again" chip (see runner._suggestions).
        update["last_visit"] = last_visit
    return update
