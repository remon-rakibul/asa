"""HIS/EMR inbound sync endpoint.

Hospitals push schedule updates to this endpoint. Guarded by a shared secret
set in HIS_WEBHOOK_SECRET. No-op if the secret is unconfigured.
"""

from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, Depends, Header, HTTPException

from config import settings
from tools.database import get_clinic, get_pool, save_schedule

log = logging.getLogger(__name__)

router = APIRouter(prefix="/his", tags=["his"])


async def _verify_his_secret(
    x_his_secret: str | None = Header(None, alias="X-HIS-Secret"),
) -> None:
    if not settings.his_webhook_secret:
        raise HTTPException(status_code=503, detail="HIS integration not configured")
    # Constant-time compare (matches the WhatsApp webhook) so the shared secret
    # can't be recovered byte-by-byte via response timing.
    if not hmac.compare_digest(x_his_secret or "", settings.his_webhook_secret):
        raise HTTPException(status_code=401, detail="Invalid HIS secret")


@router.post("/sync")
async def his_sync(
    payload: dict,
    _: None = Depends(_verify_his_secret),
) -> dict:
    """Receive a push event from the hospital's HIS.

    Supported events:
    - "schedule_updated": { "clinic_id": int, "slots": [...schedule rows...] }

    Unknown events are logged and acknowledged (no error) to avoid breaking
    HIS retry loops.
    """
    event = payload.get("event", "unknown")
    log.info("HIS sync event received: %s", event)

    if event == "schedule_updated":
        clinic_id = payload.get("clinic_id")
        slots = payload.get("slots", [])
        if clinic_id and slots:
            try:
                cid = int(clinic_id)
            except (TypeError, ValueError):
                raise HTTPException(status_code=422, detail="clinic_id must be an integer")
            # Verify the clinic exists before overwriting its schedule.
            if not await get_clinic(cid):
                raise HTTPException(status_code=404, detail=f"Clinic {cid} not found")
            await save_schedule(cid, slots)
            log.info("HIS schedule_updated: clinic_id=%s, %d rows", cid, len(slots))
        return {"ok": True, "event": event, "processed": True}

    # Unknown events: acknowledge without error.
    return {"ok": True, "event": event, "processed": False}
