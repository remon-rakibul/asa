"""WhatsApp voice calls → the ASA voice agent, via the LiveKit WhatsApp Connector.

Flow for a patient pressing the call button on the ASA WhatsApp number:

  1. Meta POSTs a `calls` webhook (event=connect) carrying the call id and the
     caller's SDP offer to /whatsapp/webhook.
  2. accept_inbound_call() hands that offer to LiveKit's AcceptWhatsAppCall.
     LiveKit answers the call with Meta, bridges the audio into a new room and
     dispatches the voice worker (main.py) with JSON metadata describing the
     scope — exactly like a browser-portal call, so the worker needs no
     WhatsApp-specific routing.
  3. When the patient hangs up Meta sends event=terminate → end_call() tells
     LiveKit (USER_INITIATED) so the room and agent are torn down at once
     instead of after LiveKit's 30 s idle cleanup.

Scope reuses the messaging router (api/messaging.resolve_message_scope): a
hospital's own WhatsApp number keeps its clinic, the ASA number runs platform
mode, and a caller whose phone is verified on a patient account joins that
account's unified thread — the same conversation as their chats and messages.
Anonymous callers continue their `whatsapp-<number>` thread, so a call picks up
where their WhatsApp chat left off.
"""

from __future__ import annotations

import json
import logging
import uuid

from config import settings
from utils.text import normalize_bd_mobile

log = logging.getLogger(__name__)


def calling_configured() -> bool:
    return bool(
        settings.whatsapp_calling_enabled
        and settings.whatsapp_token
        and settings.whatsapp_phone_id
        and settings.livekit_url
        and settings.livekit_api_key
        and settings.livekit_api_secret
    )


def _lkapi():
    from livekit import api as lk

    return lk.LiveKitAPI(settings.livekit_url, settings.livekit_api_key, settings.livekit_api_secret)


def build_dispatch_metadata(scope: dict, caller: str, call_id: str, business_number: str) -> dict:
    """Voice-worker dispatch metadata for one call (read by main._resolve_channel).

    `scope` is api.messaging.resolve_message_scope("whatsapp", ...) output.
    """
    turn = scope.get("turn") or {}
    mobile = normalize_bd_mobile(caller)
    return {
        "whatsapp_call": True,
        "whatsapp_call_id": call_id,
        "business_number": business_number,
        "clinic_id": turn.get("clinic_id"),
        "hospital_id": None,
        "platform": bool(turn.get("platform")),
        # The LangGraph thread this caller already chats on — the call joins it.
        "thread_id": scope.get("session_id"),
        "patient_account_id": turn.get("patient_account_id"),
        "patient_name": turn.get("patient_name"),
        "patient_phone": turn.get("patient_mobile") or (mobile if len(mobile) == 11 else None),
        "caller": caller,
    }


async def accept_inbound_call(call: dict, phone_number_id: str, business_number: str) -> str | None:
    """Answer one user-initiated call via the LiveKit connector.

    Returns the LiveKit room name, or None when the call could not be accepted
    (it then rings out on the patient's side). Never raises.
    """
    from livekit import api as lk
    from livekit.protocol.rtc import SessionDescription

    from api.messaging import resolve_message_scope

    call_id = call.get("id") or ""
    caller = call.get("from") or ""
    session = call.get("session") or {}
    sdp = session.get("sdp") or ""
    if not (call_id and caller and sdp):
        log.warning("WhatsApp call webhook missing id/from/sdp — ignored")
        return None
    if not calling_configured():
        log.warning("WhatsApp call %s received but calling is not configured", call_id)
        return None

    try:
        scope = await resolve_message_scope("whatsapp", business_number, caller)
    except Exception:
        log.exception("scope resolution failed for WhatsApp call %s — using platform mode", call_id)
        scope = {"session_id": f"whatsapp-{caller}", "turn": {"channel": "whatsapp", "platform": True}}
    metadata = build_dispatch_metadata(scope, caller, call_id, business_number)

    lkapi = _lkapi()
    try:
        resp = await lkapi.connector.accept_whatsapp_call(lk.AcceptWhatsAppCallRequest(
            whatsapp_phone_number_id=phone_number_id or settings.whatsapp_phone_id,
            whatsapp_api_key=settings.whatsapp_token,
            whatsapp_cloud_api_version=settings.whatsapp_cloud_api_version,
            whatsapp_call_id=call_id,
            sdp=SessionDescription(type=session.get("sdp_type") or "offer", sdp=sdp),
            # No phone number in the room name — room names show up in logs/dashboards.
            room_name=f"wa-call-{uuid.uuid4().hex[:12]}",
            participant_identity=f"wa-{call_id[-12:]}",
            participant_name="WhatsApp caller",
            agents=[lk.RoomAgentDispatch(
                agent_name=settings.livekit_agent_name,
                metadata=json.dumps(metadata),
            )],
        ))
    except Exception:
        log.exception("LiveKit AcceptWhatsAppCall failed for %s", call_id)
        return None
    finally:
        await lkapi.aclose()
    log.info("WhatsApp call %s accepted into room %s (platform=%s, account=%s)",
             call_id, resp.room_name, metadata["platform"], metadata["patient_account_id"])
    return resp.room_name


async def dial_patient(to: str, business_number: str = "") -> dict | None:
    """ASA calls a patient (business-initiated call) via the LiveKit connector.

    Needs the patient's call permission (granted in WhatsApp — see
    request_call_permission) and the number's calling enabled. The phone rings
    immediately; when the patient answers, Meta sends a `calls` connect webhook
    with the SDP answer, which connect_outbound_call() completes. Returns
    {"call_id", "room_name"} or None. Never raises.
    """
    from livekit import api as lk

    from api.messaging import resolve_message_scope

    if not calling_configured():
        log.warning("Outbound WhatsApp call to %s skipped: calling not configured", to)
        return None
    caller = "".join(ch for ch in to if ch.isdigit())
    try:
        scope = await resolve_message_scope("whatsapp", business_number, caller)
    except Exception:
        scope = {"session_id": f"whatsapp-{caller}", "turn": {"channel": "whatsapp", "platform": True}}
    metadata = build_dispatch_metadata(scope, caller, "", business_number)
    metadata["direction"] = "outbound"

    lkapi = _lkapi()
    try:
        resp = await lkapi.connector.dial_whatsapp_call(lk.DialWhatsAppCallRequest(
            whatsapp_phone_number_id=settings.whatsapp_phone_id,
            whatsapp_to_phone_number=caller,
            whatsapp_api_key=settings.whatsapp_token,
            whatsapp_cloud_api_version=settings.whatsapp_cloud_api_version,
            room_name=f"wa-call-{uuid.uuid4().hex[:12]}",
            participant_identity=f"wa-out-{uuid.uuid4().hex[:8]}",
            participant_name="WhatsApp patient",
            agents=[lk.RoomAgentDispatch(
                agent_name=settings.livekit_agent_name,
                metadata=json.dumps(metadata),
            )],
        ))
    except Exception:
        log.exception("LiveKit DialWhatsAppCall to %s failed", caller)
        return None
    finally:
        await lkapi.aclose()
    log.info("Outbound WhatsApp call %s ringing (room %s)", resp.whatsapp_call_id, resp.room_name)
    return {"call_id": resp.whatsapp_call_id, "room_name": resp.room_name}


async def connect_outbound_call(call: dict) -> bool:
    """Patient answered an ASA-initiated call: pass Meta's SDP answer to LiveKit.

    Must run promptly — the patient is already on the line. Never raises.
    """
    from livekit import api as lk
    from livekit.protocol.rtc import SessionDescription

    call_id = call.get("id") or ""
    session = call.get("session") or {}
    if not (call_id and session.get("sdp")):
        log.warning("Outbound call connect webhook missing id/sdp — ignored")
        return False
    lkapi = _lkapi()
    try:
        await lkapi.connector.connect_whatsapp_call(lk.ConnectWhatsAppCallRequest(
            whatsapp_call_id=call_id,
            sdp=SessionDescription(type=session.get("sdp_type") or "answer", sdp=session["sdp"]),
        ))
    except Exception:
        log.exception("LiveKit ConnectWhatsAppCall failed for %s", call_id)
        return False
    finally:
        await lkapi.aclose()
    log.info("Outbound WhatsApp call %s connected", call_id)
    return True


async def end_call(call_id: str) -> None:
    """Patient hung up (Meta terminate webhook): release the LiveKit session.

    USER_INITIATED needs no Meta API call. Meta also sends terminate when WE
    end the call, in which case LiveKit has already cleaned up and replies
    with an error — expected, logged at debug.
    """
    from livekit import api as lk

    if not call_id or not calling_configured():
        return
    lkapi = _lkapi()
    try:
        await lkapi.connector.disconnect_whatsapp_call(lk.DisconnectWhatsAppCallRequest(
            whatsapp_call_id=call_id,
            disconnect_reason=lk.DisconnectWhatsAppCallRequest.USER_INITIATED,
        ))
    except Exception as exc:
        log.debug("DisconnectWhatsAppCall(%s) skipped: %s", call_id, type(exc).__name__)
    finally:
        await lkapi.aclose()
