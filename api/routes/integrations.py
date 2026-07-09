"""Integrations & channels admin endpoints.

GET    /integrations        -> platform capability status (no secrets)
GET    /channels            -> this clinic's channel mappings
POST   /channels            -> map a WhatsApp/SMS/voice number to this clinic
DELETE /channels/{id}       -> remove a mapping
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from config import settings
from tools.database import add_channel, delete_channel, get_channel_stats, list_channels

from ..deps import audit_action, current_clinic_id, current_user
from ..schemas import (
    ChannelCreate,
    ChannelOut,
    ChannelStats,
    IntegrationItem,
    IntegrationsStatus,
)

router = APIRouter(tags=["integrations"])


@router.get("/integrations", response_model=IntegrationsStatus)
async def integrations(_: int = Depends(current_clinic_id)) -> IntegrationsStatus:
    """Report which platform integrations are configured. Booleans + safe
    metadata only — never returns tokens/keys."""
    wa = bool(settings.whatsapp_token and settings.whatsapp_phone_id)

    if settings.sms_provider == "twilio":
        sms_ok = bool(settings.twilio_account_sid and settings.twilio_from_number)
        sms_detail = "Twilio" + ("" if sms_ok else " (credentials missing)")
    elif settings.sms_provider == "bdgateway":
        sms_ok = bool(settings.bd_sms_api_url and settings.bd_sms_api_key)
        sms_detail = "BD gateway" + ("" if sms_ok else " (credentials missing)")
    else:
        sms_ok = False
        sms_detail = "Disabled (SMS_PROVIDER=none)"

    voice_ok = bool(settings.livekit_url and settings.livekit_api_key)

    items = [
        IntegrationItem(
            key="whatsapp", name="WhatsApp (Meta Cloud API)", configured=wa,
            detail="Connected" if wa else "Set WHATSAPP_TOKEN + WHATSAPP_PHONE_ID",
        ),
        IntegrationItem(
            key="sms", name="SMS", configured=sms_ok, detail=sms_detail,
        ),
        IntegrationItem(
            key="voice", name="Voice calls (LiveKit SIP)", configured=voice_ok,
            detail=settings.livekit_url if voice_ok else "Set LIVEKIT_URL + keys + a SIP trunk",
        ),
        IntegrationItem(
            key="llm", name="Language model (Ollama)", configured=True,
            detail=settings.ollama_model,
        ),
        IntegrationItem(
            key="tts", name="Text-to-speech", configured=True,
            detail=f"{settings.tts_engine} (fallback: {settings.tts_fast_engine})",
        ),
        IntegrationItem(
            key="stt", name="Speech-to-text (Whisper)", configured=True,
            detail=f"{settings.whisper_model} on {settings.whisper_device}",
        ),
    ]
    return IntegrationsStatus(items=items)


@router.get("/channels", response_model=list[ChannelOut])
async def get_channels(clinic_id: int = Depends(current_clinic_id)):
    return await list_channels(clinic_id)


@router.post("/channels", response_model=ChannelOut, status_code=201)
async def create_channel(
    body: ChannelCreate, request: Request,
    clinic_id: int = Depends(current_clinic_id),
    user: dict = Depends(current_user),
):
    if body.kind == "voice_ivr":
        raise HTTPException(
            status_code=400,
            detail="voice_ivr channels are hospital-level. Use POST /hospitals/{id}/channels.",
        )
    row = await add_channel(
        clinic_id=clinic_id,
        kind=body.kind,
        identifier=body.identifier.strip(),
        label=(body.label or "").strip() or None,
    )
    if row is None:
        raise HTTPException(
            status_code=409,
            detail="That number/identifier is already mapped to a clinic.",
        )
    await audit_action(
        user, request, action="add_channel", entity_type="channel",
        entity_id=row.get("id"), clinic_id=clinic_id,
        new_value={"kind": body.kind, "identifier": body.identifier.strip()},
    )
    return row


@router.get("/channels/stats", response_model=list[ChannelStats])
async def channel_stats(clinic_id: int = Depends(current_clinic_id)):
    """Call and appointment counts per voice number configured for this clinic."""
    return await get_channel_stats(clinic_id)


@router.delete("/channels/{channel_id}", status_code=204)
async def remove_channel(
    channel_id: int, request: Request,
    clinic_id: int = Depends(current_clinic_id),
    user: dict = Depends(current_user),
):
    ok = await delete_channel(clinic_id, channel_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Channel not found")
    await audit_action(
        user, request, action="remove_channel", entity_type="channel",
        entity_id=channel_id, clinic_id=clinic_id,
    )
