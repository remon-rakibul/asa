"""Escalation queue — conversations the agent flagged for human follow-up.

GET   /escalations            -> open (default) or resolved escalations
PATCH /escalations/{id}       -> mark resolved

Staff answer the patient from the Conversations view (POST
/conversations/{session_id}/reply), which also reaches web-portal threads.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from tools.database import list_escalations, resolve_escalation

from ..deps import current_user

router = APIRouter(prefix="/escalations", tags=["escalations"])


class EscalationOut(BaseModel):
    id: int
    clinic_id: int | None
    hospital_id: int | None = None
    session_id: str
    channel: str
    reason: str
    status: str
    created_at: datetime
    resolved_at: datetime | None


@router.get("", response_model=list[EscalationOut])
async def escalations(status: str = "open", user: dict = Depends(current_user)):
    hospital_id = user.get("hospital_id")
    if user.get("role") == "hospital_admin" and hospital_id:
        # Wider than one department: includes hospital-level escalations
        # (raised before any department was chosen on a unified thread).
        return await list_escalations(hospital_id=hospital_id, status=status)
    clinic_id = user.get("clinic_id")
    if not clinic_id:
        raise HTTPException(status_code=403, detail="This account is not scoped to a clinic")
    return await list_escalations(clinic_id=clinic_id, status=status)


@router.patch("/{escalation_id}")
async def resolve(escalation_id: int, user: dict = Depends(current_user)) -> dict:
    hospital_id = user.get("hospital_id")
    if user.get("role") == "hospital_admin" and hospital_id:
        ok = await resolve_escalation(escalation_id, hospital_id=hospital_id)
    else:
        clinic_id = user.get("clinic_id")
        if not clinic_id:
            raise HTTPException(status_code=403, detail="This account is not scoped to a clinic")
        ok = await resolve_escalation(escalation_id, clinic_id=clinic_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Escalation not found or already resolved")
    return {"ok": True}
