"""Queue / token management endpoints for hospital OPD departments."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from api.deps import audit_action, current_hospital_id, require_role
from api.schemas import QueueStatus, TokenOut
from tools.database import call_token, complete_token, list_tokens_today
from tools.sms import send_token_notification

router = APIRouter(prefix="/queue", tags=["queue"])


def _clinic_scope(user: dict) -> int | None:
    """The caller's own clinic_id for tenant scoping, or None for platform_admin
    (intentionally cross-tenant — matches GET /clinics elsewhere). Any other
    role without a clinic_id is a data inconsistency; scoping stays strict
    rather than silently falling back to unrestricted access."""
    if user.get("role") == "platform_admin":
        return None
    cid = user.get("clinic_id")
    if not cid:
        raise HTTPException(status_code=403, detail="This account is not scoped to a clinic")
    return int(cid)


@router.get("/{department_id}/today", response_model=QueueStatus)
async def get_todays_queue(
    department_id: int,
    user: dict = Depends(
        require_role("hospital_admin", "dept_head", "receptionist", "platform_admin")
    ),
    hospital_id: int | None = Depends(current_hospital_id),
) -> dict:
    """Return today's token queue for a department (for display boards / receptionists)."""
    clinic_id = _clinic_scope(user)
    tokens = await list_tokens_today(department_id, hospital_id=hospital_id, clinic_id=clinic_id)

    waiting = [t for t in tokens if t["status"] == "waiting"]
    called = next(
        (t for t in tokens if t["status"] in ("called", "in_progress")), None
    )
    current_token = called["token_number"] if called else None

    return {
        "current_token": current_token,
        "waiting_count": len(waiting),
        "tokens": tokens,
    }


@router.post("/{token_id}/call", response_model=TokenOut)
async def call_next_token(
    token_id: int, request: Request,
    user: dict = Depends(require_role("hospital_admin", "dept_head", "receptionist", "platform_admin")),
    hospital_id: int | None = Depends(current_hospital_id),
) -> dict:
    """Mark a token as called; fires an SMS notification to the patient."""
    token = await call_token(token_id, hospital_id=hospital_id, clinic_id=_clinic_scope(user))
    if not token:
        raise HTTPException(
            status_code=404, detail="Token not found or already called"
        )
    await audit_action(
        user, request, action="call_token", entity_type="token", entity_id=token_id,
        new_value={"token_number": token.get("token_number")},
    )
    # Best-effort SMS: patient is notified their turn is ready.
    from tools.database import get_pool
    from tools.crypto import decrypt_field
    pool = await get_pool()
    async with pool.acquire() as conn:
        appt = await conn.fetchrow(
            "SELECT patient_name, patient_mobile, clinic_id FROM appointments WHERE id = $1::uuid",
            token["appointment_id"],
        )
    if appt and appt["patient_mobile"]:
        token_label = f"{token.get('token_prefix','A')}{token['token_number']}"
        try:
            await send_token_notification(
                decrypt_field(appt["patient_mobile"]),
                decrypt_field(appt["patient_name"]),
                token_label,
                clinic_id=appt["clinic_id"],
            )
        except Exception:
            pass  # SMS failure must not fail the API call
    return token


@router.post("/{token_id}/complete")
async def complete_patient_visit(
    token_id: int, request: Request,
    user: dict = Depends(require_role("hospital_admin", "dept_head", "receptionist", "doctor", "platform_admin")),
    hospital_id: int | None = Depends(current_hospital_id),
) -> dict:
    success = await complete_token(token_id, hospital_id=hospital_id, clinic_id=_clinic_scope(user))
    if not success:
        raise HTTPException(status_code=404, detail="Token not found or not in active state")
    await audit_action(
        user, request, action="complete_token", entity_type="token", entity_id=token_id,
    )
    return {"ok": True}
