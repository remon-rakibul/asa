"""Turn-running helpers shared by the FastAPI backend and voice pipeline.

Drive the compiled LangGraph for a single conversational turn.
Only AIMessages with spoken text (no tool_calls) reach the UI or TTS.
Tool calls and their results are internal to the ReAct loop.
"""

from __future__ import annotations

import logging
import re
from typing import AsyncIterator

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command

from tools.database import log_turn
from utils.text import looks_fabricated_listing, sanitize_text

log = logging.getLogger(__name__)

# Longest prefix of the internal "[datetime=...]" tag that could still be
# forming (see _safe_len below). "sanitize_text" only strips the tag once it's
# fully closed by "]" — mid-stream, a still-forming tag ("...[datetime" before
# "=ISO]" arrives) would otherwise be flushed to the patient as visible text
# before the closing bracket streams in, and can't be retracted afterwards.
_DATETIME_OPEN = "[datetime"


def _safe_len(clean: str) -> int:
    """Length of `clean` that is safe to emit — excludes a still-forming
    "[datetime...]" tag at the end so it never reaches the patient mid-build.
    Any complete tag is already stripped by sanitize_text, so a leftover
    "[datetime" fragment here is by definition incomplete."""
    idx = clean.rfind(_DATETIME_OPEN)
    if idx != -1:
        return idx
    for length in range(min(len(_DATETIME_OPEN), len(clean)), 0, -1):
        if clean.endswith(_DATETIME_OPEN[:length]):
            return len(clean) - length
    return len(clean)

# Bangla/English yes/no tokens for interpreting a free-text (typed or spoken)
# reply to a pending confirm question. Matched on whole words — substring
# matching misfires badly ("হাসপাতাল" contains "হা", phone numbers contain "1").
# Any negation wins over any affirmation ("না করবেন" = no).
_YES_TOKENS = {
    "হ্যাঁ", "হা", "জি", "জ্বি", "নিশ্চিত", "আচ্ছা", "করুন", "করো", "হুম",
    "yes", "ok", "okay", "confirm", "sure", "1", "১",
}
_NO_TOKENS = {"না", "নাহ", "থাক", "no", "cancel", "0", "২", "2"}


def _affirmative(text: str) -> bool:
    lowered = (text or "").strip().lower()
    if not lowered:
        return False
    tokens = re.findall(r"[\wঀ-৿]+", lowered)
    if any(t in _NO_TOKENS for t in tokens):
        return False
    if "ঠিক আছে" in lowered:
        return True
    return any(t in _YES_TOKENS for t in tokens)


async def _pending_interrupt(graph, config: dict) -> bool:
    """True when this thread is paused on an interrupt (confirm question)."""
    try:
        snapshot = await graph.aget_state(config)
    except Exception:
        return False
    return bool(getattr(snapshot, "interrupts", None))


async def _reask_pending_confirm(graph, config: dict):
    """Re-surface the pending confirm question without resuming the graph.

    Used when a session-start (empty) turn lands on an interrupted thread —
    the patient never answered, so ask again instead of guessing yes/no.
    """
    snapshot = await graph.aget_state(config)
    for intr in getattr(snapshot, "interrupts", None) or ():
        value = getattr(intr, "value", None)
        if isinstance(value, dict) and value.get("question"):
            yield {"type": "confirm", **value}
    yield {"type": "end", "phase": "active", "appointment_id": None,
           "patient_name": None, "done": False}


async def _log(
    session_id: str,
    graph,
    config: dict,
    channel: str | None,
    user_text: str,
    reply: str,
    channel_identifier: str | None = None,
    fallback_clinic_id: int | None = None,
) -> None:
    """Best-effort transcript logging. Never raises.

    Attributed to wherever the thread landed by the END of this turn, not
    where the request started — a unified platform-mode thread may pick a
    hospital/clinic mid-turn (choose_doctor), and every turn is worth
    logging now that clinic_id is nullable (migration 0025), including
    turns before any department is chosen.
    """
    clinic_id = fallback_clinic_id
    hospital_id = None
    try:
        snapshot = await graph.aget_state(config)
        values = snapshot.values if snapshot else {}
        clinic_id = values.get("clinic_id", fallback_clinic_id)
        hospital_id = values.get("hospital_id")
    except Exception:
        pass
    ch = channel or "text"
    if user_text:
        await log_turn(
            clinic_id=clinic_id,
            hospital_id=hospital_id,
            session_id=session_id,
            channel=ch,
            role="user",
            text=user_text,
            channel_identifier=channel_identifier,
        )
    if reply:
        await log_turn(
            clinic_id=clinic_id,
            hospital_id=hospital_id,
            session_id=session_id,
            channel=ch,
            role="assistant",
            text=reply,
            channel_identifier=channel_identifier,
        )


def _spoken_texts(delta: dict) -> list[str]:
    """Extract spoken text from a single node's state delta.

    Only AIMessages with non-empty content and no tool_calls are spoken.
    ToolMessages, HumanMessages, and tool-call AIMessages are all silent.
    """
    out = []
    for msg in delta.get("messages", []) or []:
        if not isinstance(msg, AIMessage):
            continue
        if getattr(msg, "tool_calls", None):
            continue
        content = getattr(msg, "content", None)
        if content:
            out.append(content)
    return out


def _turn_input(
    message: str,
    clinic_id: int | None,
    channel: str | None = None,
    session_id: str | None = None,
    channel_identifier: str | None = None,
    hospital_id: int | None = None,
    doctor_id: int | None = None,
    departments: list | None = None,
    patient_name: str | None = None,
    patient_age: int | None = None,
    patient_mobile: str | None = None,
    patient_id: int | None = None,
    patient_account_id: int | None = None,
    platform: bool = False,
) -> dict:
    payload: dict = {"messages": [HumanMessage(content=message)]}
    if platform:
        # Platform-wide assistant (no hospital chosen yet). Set once on the
        # thread and never cleared, so the tool binding and prompt head stay
        # stable even after choose_doctor lands the thread on a clinic.
        payload["platform_mode"] = True
    if clinic_id is not None:
        payload["clinic_id"] = clinic_id
    if hospital_id is not None:
        payload["hospital_id"] = hospital_id
    if doctor_id is not None:
        payload["doctor_id"] = doctor_id
    if departments is not None:
        payload["departments"] = departments
    if patient_name is not None:
        payload["patient_name"] = patient_name
    if patient_age is not None:
        payload["patient_age"] = patient_age
    if patient_mobile is not None:
        payload["patient_mobile"] = patient_mobile
    if patient_id is not None:
        payload["patient_id"] = patient_id
    if patient_account_id is not None:
        payload["patient_account_id"] = patient_account_id
    if channel is not None:
        payload["channel"] = channel
    if session_id is not None:
        payload["session_id"] = session_id
    if channel_identifier is not None:
        payload["channel_identifier"] = channel_identifier
    return payload


async def _end_event(graph, config: dict) -> dict:
    snapshot = await graph.aget_state(config)
    values = snapshot.values if snapshot else {}
    appointment_id = values.get("appointment_id")
    return {
        "type": "end",
        "phase": "farewell" if appointment_id else "active",
        "appointment_id": appointment_id,
        "patient_name": values.get("patient_name"),
        "serial_number": values.get("serial_number"),
        "slot_label": values.get("slot_label"),
        "done": bool(appointment_id),
    }




async def run_turn(
    graph,
    session_id: str,
    message: str,
    clinic_id: int | None = None,
    channel: str | None = None,
    channel_identifier: str | None = None,
) -> dict:
    """Run one turn and return the full reply once complete.

    Returns: {reply, phase, appointment_id, patient_name, done}
    """
    config = {"configurable": {"thread_id": session_id}}

    parts: list[str] = []
    async for update in graph.astream(
        _turn_input(message, clinic_id, channel, session_id, channel_identifier),
        config=config,
        stream_mode="updates",
    ):
        for delta in update.values():
            parts.extend(_spoken_texts(delta))

    reply = sanitize_text(" ".join(parts))
    await _log(session_id, graph, config, channel, message, reply,
               channel_identifier=channel_identifier, fallback_clinic_id=clinic_id)
    snapshot = await graph.aget_state(config)
    values = snapshot.values if snapshot else {}

    return {
        "reply": reply,
        "phase": "farewell" if values.get("appointment_id") else "active",
        "appointment_id": values.get("appointment_id"),
        "patient_name": values.get("patient_name"),
        "done": bool(values.get("appointment_id")),
    }


async def stream_turn(
    graph,
    session_id: str,
    message: str,
    clinic_id: int | None = None,
    channel: str | None = None,
    channel_identifier: str | None = None,
) -> AsyncIterator[dict]:
    """Run one turn, yielding each spoken message as it is produced.

    Yields {"type": "token", "text": ...} per spoken node, then a final
    {"type": "end", "phase": ..., "appointment_id": ..., "done": ...}.
    """
    config = {"configurable": {"thread_id": session_id}}

    parts: list[str] = []
    async for update in graph.astream(
        _turn_input(message, clinic_id, channel, session_id, channel_identifier),
        config=config,
        stream_mode="updates",
    ):
        for delta in update.values():
            for text in _spoken_texts(delta):
                clean = sanitize_text(text)
                if clean:
                    parts.append(clean)
                    yield {"type": "token", "text": clean}

    await _log(session_id, graph, config, channel, message, " ".join(parts),
               channel_identifier=channel_identifier, fallback_clinic_id=clinic_id)
    yield await _end_event(graph, config)


_TOOL_STATUS: dict[str, str] = {
    "get_available_slots": "সময়সূচি দেখছি...",
    "book_appointment": "অ্যাপয়েন্টমেন্ট বুক করছি...",
    "select_department": "বিভাগ নির্বাচন করছি...",
    "list_doctors": "ডাক্তারের তালিকা দেখছি...",
    "choose_doctor": "ডাক্তার নির্বাচন করছি...",
}


async def stream_turn_tokens(
    graph,
    session_id: str,
    message: str,
    clinic_id: int | None = None,
    channel: str | None = None,
    channel_identifier: str | None = None,
    hospital_id: int | None = None,
    doctor_id: int | None = None,
    departments: list | None = None,
    patient_name: str | None = None,
    patient_age: int | None = None,
    patient_mobile: str | None = None,
    patient_id: int | None = None,
    patient_account_id: int | None = None,
    resume: bool | None = None,
    suggest: bool = False,
    platform: bool = False,
) -> AsyncIterator[dict]:
    """Run one turn, streaming the spoken reply token-by-token as the LLM generates it.

    Tool calls are silent for spoken content but emit a brief
    {"type": "status", "text": "..."} so the UI can show something while
    the tool executes. Only the LLM's spoken responses reach "token" events.

    Yields {"type": "status", "text": ...} when a tool is invoked, then
    {"type": "token", "text": <delta>} per spoken chunk, then a final
    {"type": "end", ...}.

    Confirm flow: when a tool pauses on interrupt() (cancel/reschedule), a
    {"type": "confirm", "kind": ..., "question": ...} event is yielded and the
    turn ends. The next call resumes the graph — either explicitly
    (resume=True/False from the chat confirm buttons) or implicitly: free text
    (typed or spoken) on an interrupted thread is interpreted as yes/no.
    """
    config = {"configurable": {"thread_id": session_id}}

    # Immediately acknowledge the turn so the UI shows something at once.
    yield {"type": "status", "text": "ভাবছি..."}

    # Resolve how to drive an interrupted (confirm-pending) thread.
    if resume is None:
        if await _pending_interrupt(graph, config):
            if not (message or "").strip():
                # Session-start signal (empty greeting turn) on an interrupted
                # thread — e.g. a voice call opening the chat's thread mid-
                # confirm. Don't guess yes/no: re-ask the pending question.
                async for event in _reask_pending_confirm(graph, config):
                    yield event
                return
            resume = _affirmative(message)
    elif not await _pending_interrupt(graph, config):
        # Stale confirm click (thread moved on) — treat as a plain message.
        message = message or ("হ্যাঁ" if resume else "না")
        resume = None

    if resume is not None:
        turn_input = Command(resume=resume)
        message = message or ("হ্যাঁ" if resume else "না")
    else:
        turn_input = _turn_input(
            message, clinic_id, channel, session_id, channel_identifier,
            hospital_id=hospital_id, doctor_id=doctor_id, departments=departments,
            patient_name=patient_name, patient_age=patient_age,
            patient_mobile=patient_mobile, patient_id=patient_id,
            patient_account_id=patient_account_id, platform=platform,
        )

    raw = ""
    emitted = ""
    prev_node: str | None = None
    spoken_nodes: set[str] = set()
    tool_status_emitted: bool = False     # emit tool status at most once per turn
    accumulated_tool_name: str = ""       # accumulate tool name across chunks
    # Where the current ReAct hop started in raw/emitted — lets a fabricated
    # listing that streamed BEFORE the same response's tool call be retracted.
    hop_raw_len = 0
    hop_emitted_len = 0

    def _emit(text: str, node: str) -> str | None:
        nonlocal raw, emitted, prev_node
        if prev_node is not None and node != prev_node and raw and not raw.endswith(" "):
            raw += " "
        prev_node = node
        raw += text
        sanitized = sanitize_text(raw)
        clean = sanitized[:_safe_len(sanitized)]
        if clean == emitted:
            return None
        if clean.startswith(emitted):
            delta = clean[len(emitted):]
            emitted = clean
            return delta or None
        # sanitize_text removed leading characters that were already emitted
        # (e.g. ASCII list prefix "1. " stripped mid-stream). Swallow silently;
        # next chunk will extend clean beyond emitted and correct output resumes.
        emitted = clean
        return None

    interrupted = False
    try:
        async for mode, payload in graph.astream(
            turn_input,
            config=config,
            stream_mode=["messages", "updates", "custom"],
        ):
            if mode == "custom":
                # Tool-emitted UI events (e.g. {"type": "slots", ...}) — already
                # shaped for the client, forward verbatim.
                if isinstance(payload, dict) and payload.get("type"):
                    yield payload
                continue
            if mode == "messages":
                chunk, metadata = payload
                node = metadata.get("langgraph_node")
                if node not in ("call_model", "post_booking"):
                    continue  # ToolNode messages are silent
                if "internal" in (metadata.get("tags") or ()):
                    continue  # internal LLM call (summarization) — not spoken

                tcc = getattr(chunk, "tool_call_chunks", None)
                if tcc:
                    # Content that streamed earlier in THIS response was spoken
                    # before we knew a tool call was coming. If it carries an
                    # invented listing (numbered slots/dates the model made up
                    # — real listings only exist in tool results), retract it:
                    # rewind our bookkeeping and tell the UI to drop it. The
                    # node strips it from history too (see call_model).
                    hop_text = emitted[hop_emitted_len:]
                    if hop_text and looks_fabricated_listing(hop_text):
                        log.warning("retracting fabricated pre-tool text: %.120s", hop_text)
                        raw = raw[:hop_raw_len]
                        emitted = emitted[:hop_emitted_len]
                        yield {"type": "retract", "keep": hop_emitted_len}
                    # Model is generating a tool call — replace generic status with a
                    # specific one so the UI shows which tool is running.
                    for tc in tcc:
                        name = tc.get("name") or ""
                        if name:
                            accumulated_tool_name = name
                    if not tool_status_emitted:
                        status_text = _TOOL_STATUS.get(accumulated_tool_name, "অনুগ্রহ করে অপেক্ষা করুন...")
                        yield {"type": "status", "text": status_text}
                        tool_status_emitted = True
                    continue  # tool-call chunks have no spoken content

                content = getattr(chunk, "content", "") or ""
                if not content:
                    continue
                spoken_nodes.add(node)
                delta = _emit(content, node)
                if delta:
                    yield {"type": "token", "text": delta}
            else:  # "updates"
                for node, node_delta in (payload or {}).items():
                    if node == "__interrupt__":
                        # A tool paused for patient confirmation. Surface the
                        # question and let the turn end; the next call resumes.
                        interrupted = True
                        for intr in node_delta or ():
                            value = getattr(intr, "value", None)
                            if isinstance(value, dict) and value.get("question"):
                                yield {"type": "confirm", **value}
                        continue
                    if node == "tools":
                        # A fresh call_model invocation follows every tool run
                        # in the ReAct loop — its response is a different
                        # AIMessage than whatever streamed earlier this turn,
                        # so don't let an earlier "already spoken" mark
                        # suppress it (a deterministic guard can set real
                        # content post-hoc — e.g. an empty-reply fallback —
                        # without it ever streaming token-by-token).
                        spoken_nodes.discard("call_model")
                        # New hop starts here — text emitted before this point
                        # is settled and can no longer be retracted.
                        hop_raw_len = len(raw)
                        hop_emitted_len = len(emitted)
                    if node in spoken_nodes:
                        continue
                    for text in _spoken_texts(node_delta):
                        delta = _emit(text, node)
                        if delta:
                            yield {"type": "token", "text": delta}

    except Exception as exc:
        log.warning("stream_turn_tokens error for session %s (%s): %s", session_id, type(exc).__name__, exc)
        if not emitted:
            # Nothing reached the patient — let the model write its own
            # recovery line (tiny tool-free call; no canned patient text).
            from agent.nodes import compose_error_reply

            recovery = await compose_error_reply()
            if recovery:
                yield {"type": "token", "text": recovery}
                emitted = recovery
        await _log(session_id, graph, config, channel, message, emitted,
                   channel_identifier=channel_identifier, fallback_clinic_id=clinic_id)
        if emitted:
            yield {"type": "end", "phase": "active", "appointment_id": None, "patient_name": None, "done": False}
        # else: even the recovery composer failed (LLM unreachable). Ending
        # WITHOUT an `end` event marks the turn as failed to the client — the
        # web UI drops the empty bubble and shows its retry affordance.
        return

    await _log(session_id, graph, config, channel, message, emitted,
               channel_identifier=channel_identifier, fallback_clinic_id=clinic_id)
    # End FIRST so the UI unlocks the input immediately; the LLM-composed
    # quick-reply chips follow as a late event on the still-open stream.
    yield await _end_event(graph, config)
    if suggest and not interrupted:
        from agent.nodes import compose_suggestions

        snapshot = await graph.aget_state(config)
        values = snapshot.values if snapshot else {}
        items = await compose_suggestions(values, store=getattr(graph, "store", None))
        if items:
            yield {"type": "suggestions", "items": items}
