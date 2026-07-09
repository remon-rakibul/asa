"""Patient-portal browser voice call: mint a LiveKit room token.

The patient presses "talk to us" in the portal; the browser needs a short-lived
JWT to join a LiveKit room. Because the voice worker uses explicit dispatch
(`@server.rtc_session(agent_name=...)` in `main.py`), the token also carries a
`RoomConfiguration` with a `RoomAgentDispatch` so the agent is dispatched into
the patient's room on creation.

Clinic/hospital scope and patient identity are derived server-side from the
authenticated patient token (the browser never sets them) and passed to the
agent as dispatch metadata, mirroring how `/patient/chat/stream` resolves
account -> hospital -> patient (`api/routes/patient_portal.py`).
"""

from __future__ import annotations

import json
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from livekit import api
from pydantic import BaseModel

from config import settings
from tools.database import (
    get_doctor,
    get_hospital_id_for_clinic,
    get_or_create_patient,
    get_patient_account,
)

from ..deps import current_patient

router = APIRouter(prefix="/patient/voice", tags=["patient-voice"])


class VoiceTokenRequest(BaseModel):
    # Department-level call sends clinic_id; a hospital-level "talk to us" call
    # (home page, before a department is chosen) sends hospital_id for the IVR
    # greeting. At least one must be present. doctor_id rides along when the
    # patient already picked a doctor in the wizard (validated server-side).
    clinic_id: int | None = None
    hospital_id: int | None = None
    doctor_id: int | None = None


class VoiceTokenResponse(BaseModel):
    server_url: str
    participant_token: str
    room_name: str


@router.post("/token", response_model=VoiceTokenResponse)
async def mint_voice_token(
    body: VoiceTokenRequest,
    patient: dict = Depends(current_patient),
) -> VoiceTokenResponse:
    if body.clinic_id is None and body.hospital_id is None:
        raise HTTPException(status_code=400, detail="clinic_id or hospital_id is required")

    account_id = patient["account_id"]
    account = await get_patient_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    meta: dict = {
        "patient_account_id": account_id,
        "patient_name": account["name"] or None,
        "patient_phone": account["phone"] or None,
    }

    if body.clinic_id is not None:
        hospital_id = await get_hospital_id_for_clinic(body.clinic_id)
        if hospital_id is None:
            raise HTTPException(status_code=404, detail="Department not found")
        # Resolve (or create) this hospital's patient record so the spoken
        # booking links to the account, exactly like the text path.
        patient_row = await get_or_create_patient(
            hospital_id=hospital_id,
            name=account["name"],
            phone=account["phone"],
            account_id=account_id,
        )
        meta["clinic_id"] = body.clinic_id
        meta["hospital_id"] = hospital_id
        meta["patient_id"] = patient_row["id"]
        # Pre-selected doctor: trust only if it belongs to this clinic, else
        # silently drop (same policy as /patient/chat/stream).
        if body.doctor_id is not None:
            doctor_row = await get_doctor(body.doctor_id, clinic_id=body.clinic_id)
            if doctor_row:
                meta["doctor_id"] = doctor_row["id"]
    else:
        meta["hospital_id"] = body.hospital_id

    room = f"portal-voice-{uuid4().hex}"
    token = (
        api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(f"patient-{account_id}")
        .with_name(account["name"] or "Patient")
        .with_grants(
            api.VideoGrants(
                room_join=True, room=room, can_publish=True, can_subscribe=True
            )
        )
        .with_room_config(
            api.RoomConfiguration(
                agents=[
                    api.RoomAgentDispatch(
                        agent_name=settings.livekit_agent_name,
                        metadata=json.dumps(meta, ensure_ascii=False),
                    )
                ]
            )
        )
    )

    return VoiceTokenResponse(
        server_url=settings.livekit_url,
        participant_token=token.to_jwt(),
        room_name=room,
    )
