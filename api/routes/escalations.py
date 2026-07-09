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

from ..deps import current_clinic_id

router = APIRouter(prefix="/escalations", tags=["escalations"])


class EscalationOut(BaseModel):
    id: int
    clinic_id: int | None
    session_id: str
    channel: str
    reason: str
    status: str
    created_at: datetime
    resolved_at: datetime | None


@router.get("", response_model=list[EscalationOut])
async def escalations(
    status: str = "open", clinic_id: int = Depends(current_clinic_id)
):
    return await list_escalations(clinic_id, status=status)


@router.patch("/{escalation_id}")
async def resolve(
    escalation_id: int, clinic_id: int = Depends(current_clinic_id)
) -> dict:
    ok = await resolve_escalation(clinic_id, escalation_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Escalation not found or already resolved")
    return {"ok": True}
