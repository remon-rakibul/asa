"""Outbound SMS tracking endpoint (the Messages view).

GET /messages  -> recent outbound SMS for this clinic, with optional
                  kind / status filters.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from tools.database import get_sms, list_sms
from tools.sms import DEFAULT_TEMPLATES, TEMPLATE_PLACEHOLDERS, resend as resend_sms

from ..deps import current_clinic_id, current_user
from ..schemas import SmsLogOut

router = APIRouter(prefix="/messages", tags=["messages"])


@router.get("/templates")
async def sms_templates(_user: dict = Depends(current_user)) -> dict:
    """Built-in default SMS templates + their available placeholders, so the
    Settings UI can pre-fill the editable boxes with the real defaults."""
    return {"defaults": DEFAULT_TEMPLATES, "placeholders": TEMPLATE_PLACEHOLDERS}


@router.post("/{sms_id}/resend")
async def resend_message(sms_id: int, clinic_id: int = Depends(current_clinic_id)) -> dict:
    """Re-send a previously sent SMS verbatim (e.g. one that failed)."""
    row = await get_sms(sms_id, clinic_id)
    if not row:
        raise HTTPException(status_code=404, detail="Message not found")
    await resend_sms(row["to_number"], row["body"], row["kind"], clinic_id=clinic_id)
    return {"ok": True}


@router.get("", response_model=list[SmsLogOut])
async def messages(
    clinic_id: int = Depends(current_clinic_id),
    kind: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
):
    return await list_sms(clinic_id, limit=limit, kind=kind, status=status)
