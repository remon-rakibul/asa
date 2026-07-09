"""Patient self-service portal: signup, login, browse, and book.

Platform-wide patient accounts (separate from staff `users`). A patient signs up
once, browses hospitals -> departments -> doctors, and books by chatting with the
LangGraph agent. The first booking at a hospital auto-creates that hospital's
patient (MRN) record and links it to the account.
"""

from __future__ import annotations

import asyncio
import json
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent.runner import stream_turn_tokens
import secrets
from datetime import datetime, timedelta, timezone

from tools.auth import create_patient_token, hash_password, verify_password
from tools.database import (
    cancel_appointment_for_account,
    create_password_reset,
    create_patient_account,
    delete_conversation,
    get_active_password_reset,
    get_available_slots,
    get_doctor,
    get_hospital_id_for_clinic,
    get_or_create_patient,
    get_patient_account,
    get_patient_account_by_email,
    get_patient_account_by_phone,
    list_appointments_for_account,
    list_departments,
    list_doctors as list_clinic_doctors,
    list_hospitals,
    mark_password_reset_used,
    reschedule_appointment_for_account,
    update_patient_password,
)
from tools.sms import send_booking_confirmation, send_sms

from ..deps import current_patient
from ..schemas import (
    DepartmentOut,
    DoctorOut,
    HospitalOut,
    PatientAccountOut,
    PatientAppointmentOut,
    PatientChatRequest,
    PatientLogin,
    PatientSignup,
    PatientTokenResponse,
    PasswordForgotRequest,
    PasswordResetRequest,
    RescheduleRequest,
    SlotOut,
)

router = APIRouter(prefix="/patient", tags=["patient-portal"])

# Stable session ID formula — same thread resumed every time the patient returns.
def _stable_session_id(account_id: int, clinic_id: int) -> str:
    return f"pt-acc{account_id}-clinic{clinic_id}"

# Same session-id policy as the admin chat route.
_SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{8,128}$")


# --- Auth ---

@router.post("/signup", response_model=PatientTokenResponse, status_code=201)
async def signup(body: PatientSignup) -> PatientTokenResponse:
    account = await create_patient_account(
        email=body.email,
        password_hash=hash_password(body.password),
        name=body.name.strip(),
        phone=body.phone.strip(),
    )
    if account is None:
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    token = create_patient_token(account_id=account["id"])
    return PatientTokenResponse(access_token=token, account_id=account["id"])


@router.post("/login", response_model=PatientTokenResponse)
async def login(body: PatientLogin) -> PatientTokenResponse:
    account = await get_patient_account_by_email(body.email)
    if not account or not verify_password(body.password, account["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = create_patient_token(account_id=account["id"])
    return PatientTokenResponse(access_token=token, account_id=account["id"])


async def _find_account(identifier: str) -> dict | None:
    """Resolve a patient account by email or registered phone."""
    identifier = identifier.strip()
    if "@" in identifier:
        return await get_patient_account_by_email(identifier.lower())
    return await get_patient_account_by_phone(identifier)


@router.post("/password/forgot")
async def password_forgot(body: PasswordForgotRequest) -> dict:
    """Send a 6-digit SMS reset code to the account's registered phone.

    Always returns 200 (no account enumeration). The code is single-use and
    expires in 10 minutes.
    """
    account = await _find_account(body.identifier)
    if account and account.get("phone"):
        code = f"{secrets.randbelow(1_000_000):06d}"
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
        await create_password_reset(account["id"], hash_password(code), expires_at)
        try:
            await send_sms(
                account["phone"],
                f"আপনার পাসওয়ার্ড রিসেট কোড: {code} (১০ মিনিটের জন্য বৈধ)।",
            )
        except Exception:
            pass
    return {"ok": True}


@router.post("/password/reset", response_model=PatientTokenResponse)
async def password_reset(body: PasswordResetRequest) -> PatientTokenResponse:
    """Verify the SMS code and set a new password; returns a fresh login token."""
    account = await _find_account(body.identifier)
    if account:
        reset = await get_active_password_reset(account["id"])
        if reset and verify_password(body.code, reset["code_hash"]):
            await update_patient_password(account["id"], hash_password(body.new_password))
            await mark_password_reset_used(reset["id"])
            token = create_patient_token(account_id=account["id"])
            return PatientTokenResponse(access_token=token, account_id=account["id"])
    raise HTTPException(status_code=400, detail="Invalid or expired reset code.")


@router.get("/me", response_model=PatientAccountOut)
async def me(patient: dict = Depends(current_patient)) -> dict:
    account = await get_patient_account(patient["account_id"])
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


# --- Browse (any logged-in patient) ---

@router.get("/hospitals", response_model=list[HospitalOut])
async def browse_hospitals(_patient: dict = Depends(current_patient)) -> list[dict]:
    return await list_hospitals()


@router.get("/hospitals/{hospital_id}/departments", response_model=list[DepartmentOut])
async def browse_departments(
    hospital_id: int, _patient: dict = Depends(current_patient)
) -> list[dict]:
    return await list_departments(hospital_id)


@router.get("/departments/{clinic_id}/doctors", response_model=list[DoctorOut])
async def browse_doctors(
    clinic_id: int, _patient: dict = Depends(current_patient)
) -> list[dict]:
    return await list_clinic_doctors(clinic_id)


@router.get("/appointments", response_model=list[PatientAppointmentOut])
async def my_appointments(patient: dict = Depends(current_patient)) -> list[dict]:
    return await list_appointments_for_account(patient["account_id"])


@router.post("/appointments/{appointment_id}/cancel")
async def cancel_my_appointment(
    appointment_id: str, patient: dict = Depends(current_patient)
) -> dict:
    """Cancel one of the authenticated patient's own confirmed appointments."""
    ok = await cancel_appointment_for_account(patient["account_id"], appointment_id)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail="Appointment not found, not yours, or already cancelled.",
        )
    return {"ok": True}


@router.get("/departments/{clinic_id}/availability", response_model=list[SlotOut])
async def department_availability(
    clinic_id: int, _patient: dict = Depends(current_patient)
) -> list[dict]:
    """Open slots for a department, used by the patient reschedule picker."""
    return await get_available_slots(clinic_id, days_ahead=14)


@router.post("/appointments/{appointment_id}/reschedule")
async def reschedule_my_appointment(
    appointment_id: str, body: RescheduleRequest,
    patient: dict = Depends(current_patient),
) -> dict:
    """Move one of the patient's own confirmed appointments to a new slot."""
    result = await reschedule_appointment_for_account(
        patient["account_id"], appointment_id, body.slot_datetime
    )
    if result["status"] == "slot_taken":
        raise HTTPException(status_code=409, detail="That slot is already booked")
    if result["status"] != "ok":
        raise HTTPException(status_code=404, detail="Appointment not found or not reschedulable")
    appt = result["appointment"]
    try:
        await send_booking_confirmation(
            appt["patient_mobile"], appt["patient_name"],
            appt["scheduled_at"].strftime("%d %b %Y, %I:%M %p"),
            clinic_id=appt.get("clinic_id"), serial_number=appt.get("serial_number"),
        )
    except Exception:
        pass
    return {"ok": True}


# --- Booking chat (patient-scoped, streaming) ---

class PrewarmRequest(BaseModel):
    clinic_id: int
    doctor_id: int | None = None


@router.post("/chat/prewarm")
async def patient_chat_prewarm(
    body: PrewarmRequest,
    patient: dict = Depends(current_patient),
) -> dict:
    """Heat the LLM's prompt cache for this patient+department before the chat opens.

    Called fire-and-forget by the portal wizard when the patient reaches the
    doctor step — by the time they land in the chat, the greeting turn's
    prompt prefix is already prefilled and the first token arrives in seconds
    instead of minutes (CPU inference). Read-only: unlike /chat/stream it does
    NOT create the hospital's patient record.
    """
    account_id = patient["account_id"]
    account = await get_patient_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    hospital_id = await get_hospital_id_for_clinic(body.clinic_id)
    if hospital_id is None:
        raise HTTPException(status_code=404, detail="Department not found")

    from agent.nodes import prewarm_turn

    # Seed the pre-selected doctor exactly like /chat/stream does: the
    # {doctor_context} prompt section differs between PRE-SELECTED and
    # multi-doctor mode, and it sits in the prompt's static head — without
    # the same doctor_id the warmed cache prefix won't match the real turn.
    doctor_id = None
    if body.doctor_id is not None:
        doctor_row = await get_doctor(body.doctor_id, clinic_id=body.clinic_id)
        doctor_id = doctor_row["id"] if doctor_row else None

    state = {
        "clinic_id": body.clinic_id,
        "hospital_id": hospital_id,
        "doctor_id": doctor_id,
        "patient_account_id": account_id,
        "patient_name": account["name"] or None,
        "patient_mobile": account["phone"] or None,
    }
    asyncio.create_task(prewarm_turn(state))
    return {"ok": True}


@router.post("/chat/stream")
async def patient_chat_stream(
    body: PatientChatRequest,
    request: Request,
    patient: dict = Depends(current_patient),
) -> StreamingResponse:
    """Stream the booking agent for an authenticated patient in a chosen department.

    Resolves (account, clinic) -> the hospital's patient record (auto-created on
    first use) so the booked appointment is linked to this account.
    """
    if not _SESSION_ID_RE.match(body.session_id):
        raise HTTPException(
            status_code=400,
            detail="session_id must be 8-128 alphanumeric characters (hyphens/underscores allowed).",
        )

    account_id = patient["account_id"]
    account = await get_patient_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    hospital_id = await get_hospital_id_for_clinic(body.clinic_id)
    if hospital_id is None:
        raise HTTPException(status_code=404, detail="Department not found")

    # Resolve (or create) this hospital's patient record for the account.
    patient_row = await get_or_create_patient(
        hospital_id=hospital_id,
        name=account["name"],
        phone=account["phone"],
        account_id=account_id,
    )

    # Pre-selected doctor from the portal wizard: trust it only if it actually
    # belongs to this clinic — a stale/mismatched id is silently dropped rather
    # than erroring, since a bad URL param shouldn't block the whole turn.
    doctor_id = None
    if body.doctor_id is not None:
        doctor_row = await get_doctor(body.doctor_id, clinic_id=body.clinic_id)
        doctor_id = doctor_row["id"] if doctor_row else None

    graph = request.app.state.graph

    async def event_generator():
        async for event in stream_turn_tokens(
            graph,
            body.session_id,
            body.message,
            clinic_id=body.clinic_id,
            hospital_id=hospital_id,
            doctor_id=doctor_id,
            channel="web",
            patient_id=patient_row["id"],
            patient_account_id=account_id,
            patient_name=account["name"] or None,
            patient_mobile=account["phone"] or None,
            resume=body.resume,
            suggest=True,
        ):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/chat/history/{clinic_id}")
async def get_chat_history(
    clinic_id: int,
    request: Request,
    patient: dict = Depends(current_patient),
) -> list[dict]:
    """Return the saved conversation for this patient+clinic from the LangGraph checkpointer.

    Uses graph.get_state() (AsyncPostgresSaver) to load the latest thread state.
    Only HumanMessage and AIMessage are returned — tool messages are internal.
    """
    account_id = patient["account_id"]
    session_id = _stable_session_id(account_id, clinic_id)
    graph = request.app.state.graph

    try:
        config = {"configurable": {"thread_id": session_id}}
        state = await graph.aget_state(config)
        values = state.values or {}
        messages = values.get("messages", [])
    except Exception:
        return []

    result = []
    for msg in messages:
        role = getattr(msg, "type", None)
        if role == "human":
            # Skip empty greeting-trigger messages (send("") starts the agent's
            # opening turn) — they'd render as blank user bubbles after reload.
            if msg.content:
                result.append({"role": "user", "text": msg.content})
        elif role == "ai":
            content = msg.content or ""
            if content:  # skip empty AI messages (pure tool-call turns)
                result.append({"role": "assistant", "text": content})

    # Re-attach the last shown slot picker (if any, and not yet booked) to the
    # final assistant turn so the UI can render the tappable grid after a reload.
    offered = values.get("offered_slots")
    if offered and not values.get("appointment_id"):
        for item in reversed(result):
            if item["role"] == "assistant":
                item["slots"] = offered
                break

    # Pending confirm question (cancel/reschedule interrupt): re-surface it so
    # the UI re-renders the confirm card after a reload instead of stranding
    # the paused thread.
    for intr in getattr(state, "interrupts", None) or ():
        value = getattr(intr, "value", None)
        if isinstance(value, dict) and value.get("question"):
            result.append(
                {"role": "assistant", "text": value["question"], "confirm": value}
            )
            break
    return result


@router.delete("/chat/history/{clinic_id}")
async def clear_chat_history(
    clinic_id: int,
    request: Request,
    patient: dict = Depends(current_patient),
) -> dict:
    """Delete this patient+clinic conversation history so the next visit is fresh.

    Wipes both stores: the LangGraph thread state (checkpointer, in Postgres) and
    the conversation_log rows. Used by the portal "New conversation" button.
    """
    account_id = patient["account_id"]
    session_id = _stable_session_id(account_id, clinic_id)
    graph = request.app.state.graph

    checkpointer = getattr(graph, "checkpointer", None)
    if checkpointer is not None:
        try:
            await checkpointer.adelete_thread(session_id)
        except Exception:
            # Best-effort: a missing/empty thread is fine — the goal is a clean slate.
            pass

    # Also remove the logged turns (Conversations viewer / analytics).
    try:
        await delete_conversation(clinic_id, session_id)
    except Exception:
        pass

    return {"ok": True}
