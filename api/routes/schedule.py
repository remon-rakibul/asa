"""REST endpoints for the doctor's weekly schedule."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from tools.audit import record_audit
from tools.database import get_schedule, save_schedule

from ..deps import client_ip, current_clinic_id, current_user
from ..schemas import ScheduleOut, ScheduleRow

router = APIRouter(prefix="/schedule", tags=["schedule"])


@router.get("", response_model=list[ScheduleOut])
async def read_schedule(clinic_id: int = Depends(current_clinic_id)):
    return await get_schedule(clinic_id)


@router.put("")
async def replace_schedule(
    rows: list[ScheduleRow], request: Request,
    clinic_id: int = Depends(current_clinic_id),
    user: dict = Depends(current_user),
):
    await save_schedule(clinic_id, [r.model_dump() for r in rows])
    uid = user.get("user_id")
    await record_audit(
        action="update_schedule",
        entity_type="schedule",
        entity_id=str(clinic_id),
        clinic_id=clinic_id,
        hospital_id=user.get("hospital_id"),
        user_id=int(uid) if uid else None,
        actor_role=user.get("role", ""),
        new_value={"days": len(rows)},
        ip_address=client_ip(request),
    )
    return {"ok": True, "count": len(rows)}
