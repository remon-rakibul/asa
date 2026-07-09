"""REST endpoints for appointments and availability."""

from __future__ import annotations

from typing import Optional

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from tools.audit import record_audit
from tools.database import (
    cancel_appointment,
    get_available_slots,
    list_appointment_events,
    list_appointments,
    reschedule_appointment,
    set_appointment_status,
)
from tools.sms import send_booking_confirmation

from ..deps import client_ip, current_clinic_id, current_user, require_role
from ..schemas import (
    AppointmentEventOut,
    AppointmentOut,
    AppointmentStatusUpdate,
    CancelRequest,
    RescheduleRequest,
    SlotOut,
)

router = APIRouter(tags=["appointments"])

_ALLOWED_STATUS = {
    "confirmed", "checked_in", "completed", "no_show", "cancelled", "all",
}

_STAFF_ROLES = (
    "hospital_admin", "dept_head", "receptionist", "doctor", "platform_admin",
)


def _actor(user: dict) -> dict:
    """Pull (user_id, role) attribution out of the JWT claims."""
    uid = user.get("user_id")
    return {
        "actor_user_id": int(uid) if uid else None,
        "actor_role": user.get("role", ""),
    }


async def _audit(user, request, action, appointment_id, clinic_id, new_value=None):
    uid = user.get("user_id")
    await record_audit(
        action=action,
        entity_type="appointment",
        entity_id=appointment_id,
        clinic_id=clinic_id,
        hospital_id=user.get("hospital_id"),
        user_id=int(uid) if uid else None,
        actor_role=user.get("role", ""),
        new_value=new_value,
        ip_address=client_ip(request),
    )


def _validate_date(value: Optional[str], field: str) -> Optional[str]:
    if value is None:
        return None
    try:
        date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{field} must be YYYY-MM-DD")
    return value


@router.get("/appointments", response_model=list[AppointmentOut])
async def get_appointments(
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    clinic_id: int = Depends(current_clinic_id),
):
    if status is not None and status not in _ALLOWED_STATUS:
        raise HTTPException(status_code=400, detail="invalid status filter")
    rows = await list_appointments(
        clinic_id=clinic_id,
        date_from=_validate_date(date_from, "date_from"),
        date_to=_validate_date(date_to, "date_to"),
        status=status,
        q=q,
    )
    return rows


@router.patch("/appointments/{appointment_id}")
async def patch_appointment(
    appointment_id: str, body: CancelRequest, request: Request,
    clinic_id: int = Depends(current_clinic_id),
    user: dict = Depends(current_user),
):
    ok = await cancel_appointment(
        clinic_id, appointment_id, reason=body.reason, **_actor(user)
    )
    if not ok:
        raise HTTPException(
            status_code=404,
            detail="Appointment not found or already cancelled",
        )
    await _audit(user, request, "cancel_appointment", appointment_id, clinic_id,
                 new_value={"reason": body.reason} if body.reason else None)
    return {"ok": True}


@router.post("/appointments/{appointment_id}/status")
async def update_status(
    appointment_id: str, body: AppointmentStatusUpdate, request: Request,
    user: dict = Depends(require_role(*_STAFF_ROLES)),
    clinic_id: int = Depends(current_clinic_id),
):
    """Transition an appointment through its lifecycle (check-in / complete /
    no-show / cancel) with actor attribution and history."""
    result = await set_appointment_status(
        clinic_id, appointment_id, body.status, reason=body.reason, **_actor(user)
    )
    if result["status"] == "not_found":
        raise HTTPException(status_code=404, detail="Appointment not found")
    if result["status"] == "invalid":
        frm = result.get("from")
        raise HTTPException(
            status_code=409,
            detail=f"Cannot change status from '{frm}' to '{body.status}'"
                   if frm else f"Invalid status '{body.status}'",
        )
    await _audit(user, request, f"appointment_{body.status}", appointment_id, clinic_id,
                 new_value={"status": body.status})
    return {"ok": True, "appointment": result["appointment"]}


@router.get("/appointments/{appointment_id}/events", response_model=list[AppointmentEventOut])
async def appointment_events(
    appointment_id: str,
    clinic_id: int = Depends(current_clinic_id),
):
    """The change-history timeline for one appointment (newest first)."""
    return await list_appointment_events(clinic_id, appointment_id)


@router.post("/appointments/{appointment_id}/reschedule")
async def reschedule(
    appointment_id: str, body: RescheduleRequest, request: Request,
    clinic_id: int = Depends(current_clinic_id),
    user: dict = Depends(current_user),
):
    result = await reschedule_appointment(
        clinic_id, appointment_id, body.slot_datetime, **_actor(user)
    )
    if result["status"] == "slot_taken":
        raise HTTPException(status_code=409, detail="That slot is already booked")
    if result["status"] != "ok":
        raise HTTPException(status_code=404, detail="Appointment not found or not reschedulable")
    await _audit(user, request, "reschedule_appointment", appointment_id, clinic_id,
                 new_value={"slot_datetime": body.slot_datetime})
    appt = result["appointment"]
    # Best-effort confirmation SMS (logged in sms_log).
    try:
        await send_booking_confirmation(
            appt["patient_mobile"], appt["patient_name"],
            appt["scheduled_at"].strftime("%d %b %Y, %I:%M %p"),
            clinic_id=clinic_id, serial_number=appt.get("serial_number"),
        )
    except Exception:
        pass
    return {"ok": True}


@router.get("/availability", response_model=list[SlotOut])
async def availability(
    days_ahead: int = Query(7, ge=1, le=60),
    clinic_id: int = Depends(current_clinic_id),
):
    return await get_available_slots(clinic_id, days_ahead=days_ahead)
