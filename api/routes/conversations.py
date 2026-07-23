"""Conversation transcript endpoints (the Conversations view).

GET /conversations               -> recent sessions for this clinic (summaries)
GET /conversations/{session_id}  -> full transcript for one session
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from tools.database import (
    escalation_open_for_staff,
    get_conversation,
    get_conversation_for_hospital,
    list_conversations,
    list_conversations_for_hospital,
    log_turn,
)
from tools.sms import send_sms
from tools.whatsapp import send_whatsapp_text

from ..deps import current_user
from ..schemas import ConversationMessage, ConversationSummary

router = APIRouter(prefix="/conversations", tags=["conversations"])


class ReplyRequest(BaseModel):
    text: str = Field(..., min_length=1)


@router.get("", response_model=list[ConversationSummary])
async def conversations(user: dict = Depends(current_user)):
    hospital_id = user.get("hospital_id")
    if user.get("role") == "hospital_admin" and hospital_id:
        # Broader than one department: sees the hospital's whole slice of a
        # unified cross-hospital patient thread, including turns logged
        # before any department was chosen.
        return await list_conversations_for_hospital(hospital_id)
    clinic_id = user.get("clinic_id")
    if not clinic_id:
        raise HTTPException(status_code=403, detail="This account is not scoped to a clinic")
    return await list_conversations(clinic_id)


@router.get("/{session_id}", response_model=list[ConversationMessage])
async def conversation(session_id: str, user: dict = Depends(current_user)):
    return await _transcript_for_staff(user, session_id)


async def _transcript_for_staff(user: dict, session_id: str) -> list[dict]:
    """The caller's own slice of a transcript — hospital-wide for a
    hospital_admin (their clinics + hospital-level turns), clinic-scoped
    otherwise. Shared by the read/draft/reply endpoints below."""
    hospital_id = user.get("hospital_id")
    if user.get("role") == "hospital_admin" and hospital_id:
        return await get_conversation_for_hospital(hospital_id, session_id)
    clinic_id = user.get("clinic_id")
    if not clinic_id:
        raise HTTPException(status_code=403, detail="This account is not scoped to a clinic")
    return await get_conversation(clinic_id, session_id)


_DRAFT_SYSTEM = (
    "You are drafting the next reply that a human CLINIC STAFF member will send "
    "to a patient in an appointment-booking conversation. Read the transcript "
    "and write ONLY the reply text, in Bangla: warm, professional, one to three "
    "short sentences, directly addressing the patient's open question or issue. "
    "No greetings unless the patient just greeted, no markdown, no preamble."
)


@router.post("/{session_id}/draft")
async def draft_reply(
    session_id: str, user: dict = Depends(current_user),
) -> dict:
    """Compose an AI-suggested staff reply from the conversation context.

    The draft is LLM-written (never canned) and only fills the staff reply box
    for editing — it is never sent automatically.
    """
    transcript = await _transcript_for_staff(user, session_id)
    if not transcript:
        raise HTTPException(status_code=404, detail="Conversation not found")

    from langchain_core.messages import HumanMessage, SystemMessage

    from agent.nodes import _get_semaphore, _llm_plain
    from config import settings

    lines = []
    for msg in transcript[-15:]:
        role = "রোগী" if msg["role"] == "user" else "সহকারী"
        text = (msg.get("text") or "").strip()
        if text:
            lines.append(f"{role}: {text}")
    if not lines:
        raise HTTPException(status_code=404, detail="Conversation has no text")

    try:
        async with _get_semaphore():
            response = await asyncio.wait_for(
                _llm_plain().ainvoke([
                    SystemMessage(content=_DRAFT_SYSTEM),
                    HumanMessage(content="Transcript:\n" + "\n".join(lines)),
                ]),
                timeout=settings.ollama_timeout_seconds,
            )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Draft generation failed: {exc}")
    draft = getattr(response, "content", "")
    if not isinstance(draft, str) or not draft.strip():
        raise HTTPException(status_code=502, detail="Draft generation returned nothing")
    return {"draft": draft.strip()}


@router.post("/{session_id}/reply")
async def reply(
    session_id: str, body: ReplyRequest, request: Request,
    user: dict = Depends(current_user),
):
    """Staff take-over: send a manual message into the conversation's channel.

    SMS (`sms-<number>`) and WhatsApp (`whatsapp-<number>`) go out via their
    provider — clinic-scoped as before. Web-portal threads (`pt-acc…`) are a
    patient's ONE unified thread shared across every hospital they've talked
    to, so writing into it is gated: allowed only when the staff member has
    an OPEN escalation for this session (their clinic or hospital), or the
    thread is CURRENTLY on their hospital (mid-conversation, not yet
    escalated) — otherwise a hospital could inject messages into another
    hospital's conversation with this patient. Voice sessions have no
    outbound text path.
    """
    text = body.text.strip()
    role = user.get("role", "")
    clinic_id = user.get("clinic_id")
    hospital_id = user.get("hospital_id")
    number: str | None = None
    if session_id.startswith("sms-"):
        if not clinic_id:
            raise HTTPException(status_code=403, detail="This account is not scoped to a clinic")
        number = session_id[len("sms-"):]
        await send_sms(number, text, clinic_id=clinic_id)
        channel = "sms"
    elif session_id.startswith("whatsapp-"):
        if not clinic_id:
            raise HTTPException(status_code=403, detail="This account is not scoped to a clinic")
        number = session_id[len("whatsapp-"):]
        await send_whatsapp_text(number, text)
        channel = "whatsapp"
    elif session_id.startswith("pt-acc"):
        from langchain_core.messages import AIMessage

        graph = request.app.state.graph
        if role != "platform_admin":
            allowed = await escalation_open_for_staff(session_id, clinic_id, hospital_id)
            if not allowed and hospital_id:
                try:
                    snapshot = await graph.aget_state({"configurable": {"thread_id": session_id}})
                    state_hospital = (snapshot.values or {}).get("hospital_id") if snapshot else None
                except Exception:
                    state_hospital = None
                allowed = state_hospital == hospital_id
            if not allowed:
                raise HTTPException(
                    status_code=403,
                    detail="This conversation is not assigned to your hospital.",
                )
        try:
            # as_node avoids LangGraph's ambiguous-update error (several nodes
            # write `messages`) and keeps the thread resumable afterwards.
            await graph.aupdate_state(
                {"configurable": {"thread_id": session_id}},
                {"messages": [AIMessage(content=text)]},
                as_node="call_model",
            )
        except Exception:
            raise HTTPException(
                status_code=400, detail="Could not deliver to this web conversation."
            )
        channel = "web"
    else:
        raise HTTPException(
            status_code=400,
            detail="Replies are only supported for SMS, WhatsApp, and portal-chat conversations.",
        )
    # Record the staff message so it shows in the transcript, attributed to
    # the replying staff member's own clinic/hospital.
    await log_turn(
        clinic_id=clinic_id, hospital_id=hospital_id, session_id=session_id,
        channel=channel, role="assistant", text=text, channel_identifier=number,
    )
    return {"ok": True}
