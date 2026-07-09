"""Audit log read endpoints (hospital_admin only)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from api.deps import current_hospital_id, require_role
from api.schemas import AuditEntryOut
from tools.audit import list_audit_actions, list_audit_log

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=list[AuditEntryOut])
async def get_audit_log(
    entity_type: str | None = Query(None),
    action: str | None = Query(None),
    user_id: int | None = Query(None),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    user: dict = Depends(require_role("hospital_admin", "platform_admin")),
    hospital_id: int | None = Depends(current_hospital_id),
) -> list[dict]:
    """Return recent audit entries for the caller's hospital, with filters."""
    clinic_id = user.get("clinic_id")
    return await list_audit_log(
        hospital_id=hospital_id,
        clinic_id=int(clinic_id) if clinic_id else None,
        entity_type=entity_type,
        action=action,
        user_id=user_id,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
    )


@router.get("/actions")
async def get_audit_actions(
    user: dict = Depends(require_role("hospital_admin", "platform_admin")),
    hospital_id: int | None = Depends(current_hospital_id),
) -> dict:
    """Distinct action / entity_type values for the audit filter dropdowns."""
    clinic_id = user.get("clinic_id")
    return await list_audit_actions(
        hospital_id=hospital_id,
        clinic_id=int(clinic_id) if clinic_id else None,
    )
