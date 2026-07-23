"""LiveKit voice worker for the Bangla appointment-setter agent.

Pipeline:  Silero VAD -> faster-whisper STT -> LangGraph agent -> neural TTS
           (Indic Parler-TTS, with a fast fallback).

Run modes (livekit-agents CLI):
  python main.py console   # talk via your laptop mic/speakers (no LiveKit server)
  python main.py dev       # connect to a LiveKit room (hot reload)
  python main.py start     # production

Telephony: inbound phone calls arrive via a SIP trunk terminated into LiveKit
SIP (see docs/TELEPHONY.md). A DID can be mapped to one clinic ('voice_sip')
or one hospital IVR ('voice_ivr'); unmapped calls — including every call to
the platform's main number — run the cross-hospital platform agent
(VOICE_FALLBACK_SCOPE=platform, the default).

Requires the FastAPI/agent deps plus: faster-whisper, espeak-ng (system pkg),
and (for the default TTS) torch + transformers + parler-tts.
Postgres + Ollama must be running (same as the FastAPI backend).
"""

from __future__ import annotations

import json
import logging
import re
import uuid

from typing import Optional

from dotenv import load_dotenv

# Export .env into the process environment so LiveKit's own os.environ lookups
# (Inference gateway auth, turn detector) see LIVEKIT_API_KEY/SECRET etc.
# pydantic-settings alone only populates `settings`, not os.environ.
load_dotenv()

from livekit import agents
from livekit.agents import (
    AgentSession,
    AgentServer,
    RoomInputOptions,
    inference,
    stt,
)
from livekit.plugins import silero

from config import settings
from agent.graph import build_graph
from tools.database import (
    get_channel_by_kind_and_identifier,
    get_channel_scope,
    get_clinic_id_by_channel,
    get_default_clinic_id,
    get_verified_account_by_phone,
    list_departments,
    patient_tier,
)
from tools.sms import send_sms
from utils.text import normalize_bd_mobile
from voice.assistant import DoctorAssistant
from voice.langgraph_llm import LangGraphLLM
from voice.stt_factory import build_stt
from voice.tts_factory import build_tts

log = logging.getLogger(__name__)

# Loaded once per job process.
_vad = None


def _get_vad():
    global _vad
    if _vad is None:
        _vad = silero.VAD.load()
    return _vad


def _setup_process(proc: agents.JobProcess) -> None:
    """Prewarm hook: load Silero VAD when the job process starts, not on the
    first call — shaves seconds off call-connect time."""
    proc.userdata["vad"] = _get_vad()


# load_threshold: CPU utilisation above which this worker stops accepting new
# calls; LiveKit dispatches them to another replica. Scale voice capacity by
# running more replicas of `python main.py start`.
server = AgentServer(
    setup_fnc=_setup_process,
    load_threshold=settings.voice_load_threshold,
)


def _parse_job_metadata(ctx: agents.JobContext) -> Optional[dict]:
    """Parse the JSON dispatch metadata attached to a browser-portal job.

    The patient-portal voice-token endpoint (api/routes/voice.py) sets this on the
    RoomAgentDispatch; telephony jobs have none. Returns the dict or None.
    """
    try:
        raw = getattr(ctx.job, "metadata", None)
    except Exception:
        raw = None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


async def _resolve_channel(ctx: agents.JobContext) -> dict:
    """Resolve the clinic/hospital scope and patient identity for this call.

    Browser-portal rooms carry clinic/hospital + patient identity as JSON dispatch
    metadata (preferred). Telephony rooms instead map the dialed number (DID) in
    ``ctx.room.name`` to a clinic (voice_sip/voice) or hospital IVR (voice_ivr).
    Calls matching nothing — which includes every call to the platform's main
    number, since per-caller dispatch rooms are named after the CALLER — run in
    platform mode (cross-hospital search/RAG/booking), unless
    VOICE_FALLBACK_SCOPE=default_clinic restores the legacy single-clinic
    fallback.

    Returns a scope dict with keys: clinic_id, hospital_id, identifier, departments,
    patient. For voice_ivr / hospital-level browser calls, hospital_id is set and
    clinic_id is None. ``patient`` is a dict (browser portal) or None (telephony).
    """
    meta = _parse_job_metadata(ctx)
    if meta and (meta.get("clinic_id") or meta.get("hospital_id") or meta.get("platform")):
        clinic_id = meta.get("clinic_id")
        hospital_id = meta.get("hospital_id")
        # Hospital-level (no chosen department) browser call → load departments so
        # the agent can run the same IVR-style "which department?" greeting.
        # Platform-level (neither id, meta.platform) → the agent searches
        # doctors across all hospitals via search_doctors instead.
        departments = None
        if clinic_id is None and hospital_id:
            departments = await list_departments(hospital_id)
        return {
            "clinic_id": clinic_id,
            "hospital_id": hospital_id,
            "platform": bool(meta.get("platform")),
            # Doctor pre-selected in the portal wizard (validated by the token
            # endpoint); telephony scopes never set this key.
            "doctor_id": meta.get("doctor_id"),
            "identifier": None,
            "departments": departments,
            "patient": {
                "account_id": meta.get("patient_account_id"),
                "patient_id": meta.get("patient_id"),
                "name": meta.get("patient_name"),
                "phone": meta.get("patient_phone"),
            },
        }

    dialed = None
    try:
        dialed = getattr(ctx.room, "name", None)
    except Exception:
        dialed = None

    if dialed:
        # Try voice_ivr first (hospital-level IVR)
        scope = await get_channel_scope("voice_ivr", dialed)
        if scope["hospital_id"]:
            departments = await list_departments(scope["hospital_id"])
            return {**scope, "platform": False, "departments": departments, "patient": None}

        # Then clinic-level voice channels
        for kind in ("voice_sip", "voice"):
            ch = await get_channel_by_kind_and_identifier(kind, dialed)
            if ch is not None:
                return {
                    "clinic_id": ch["clinic_id"],
                    "hospital_id": None,
                    "platform": False,
                    "identifier": ch["identifier"],
                    "departments": None,
                    "patient": None,
                }

    if settings.voice_fallback_scope == "default_clinic":
        cid = await get_default_clinic_id()
        return {
            "clinic_id": cid, "hospital_id": None, "platform": False,
            "identifier": None, "departments": None, "patient": None,
        }
    return {
        "clinic_id": None, "hospital_id": None, "platform": True,
        "identifier": None, "departments": None, "patient": None,
    }


_PHONEISH = re.compile(r"\+?\d{10,13}")


def _caller_number(ctx: agents.JobContext) -> Optional[str]:
    """The caller's BD mobile (0XXXXXXXXXX), or None if it can't be determined.

    Prefers the SIP participant attribute; falls back to parsing the room name
    (individual dispatch rules name the room after the caller + a random
    suffix, so the number is extracted by pattern, never by stripping the
    whole name — the suffix may itself contain digits)."""
    try:
        participants = list((ctx.room.remote_participants or {}).values())
    except Exception:
        participants = []
    for p in participants:
        raw = (getattr(p, "attributes", None) or {}).get("sip.phoneNumber", "")
        if raw:
            mobile = normalize_bd_mobile(raw)
            if len(mobile) == 11 and mobile.startswith("01"):
                return mobile
    name = getattr(getattr(ctx, "room", None), "name", "") or ""
    m = _PHONEISH.search(name)
    if m:
        mobile = normalize_bd_mobile(m.group())
        if len(mobile) == 11 and mobile.startswith("01"):
            return mobile
    return None


async def _gate_platform_caller(ctx: agents.JobContext, scope: dict) -> dict:
    """Premium gate for the platform number (VOICE_PREMIUM_GATE).

    Only telephony calls in the platform fallback are gated — browser calls
    carry a patient identity (and are already tier-gated by the 402 on the
    token endpoint), and hospital/clinic DIDs mapped in `channels` are the
    hospital's own line. The caller's number must match an account that
    completed the ONE-TIME OTP phone verification AND be premium/trial;
    matching callers join their unified account thread (bookings linked,
    manage tools available). Everyone else gets scope["denied"]=True — the
    session speaks an LLM-composed decline, SMSes an upgrade link, and ends.
    """
    if not scope.get("platform") or scope.get("patient") is not None:
        return scope
    caller = _caller_number(ctx)
    account = await get_verified_account_by_phone(caller) if caller else None
    if account and patient_tier(account) in ("premium", "trial"):
        log.info("platform call from verified %s (account %s)", caller, account["id"])
        return {
            **scope,
            "patient": {
                "account_id": account["id"], "patient_id": None,
                "name": account.get("name") or None, "phone": account["phone"],
            },
        }
    log.info("platform call declined: caller=%s verified=%s", caller, bool(account))
    return {**scope, "denied": True, "caller": caller}


async def _hangup(ctx: agents.JobContext) -> None:
    """End the call for real — deleting the room disconnects the SIP leg."""
    try:
        from livekit import api as lk_api
        await ctx.api.room.delete_room(lk_api.DeleteRoomRequest(room=ctx.room.name))
    except Exception:
        log.warning("hangup via delete_room failed", exc_info=True)
        try:
            ctx.shutdown(reason="premium gate declined")
        except Exception:
            pass


def _room_input_options() -> RoomInputOptions | None:
    """Build call input options, optionally with LiveKit voice-isolation noise
    cancellation (clearer STT in noisy clinics). Off unless settings enable it AND
    the optional plugin is installed; returns None so session.start uses defaults.
    """
    if not settings.voice_noise_cancellation:
        return None
    try:
        from livekit.plugins import noise_cancellation  # optional dependency
    except Exception:
        log.warning(
            "voice_noise_cancellation is on but livekit-plugins-noise-cancellation "
            "is not installed — continuing without it."
        )
        return None
    return RoomInputOptions(noise_cancellation=noise_cancellation.BVC())


def _voice_session_id(scope: dict) -> str:
    """LangGraph thread id for a voice call.

    A logged-in portal patient has ONE unified thread for everything — web
    chat and voice, from any page — so a call continues the chat's context
    and the call transcript shows up in the chat history afterwards. Matches
    lib/api.ts stablePlatformSessionId() and patient_portal.py
    _platform_session_id(). Any clinic/doctor chosen before the call is
    per-turn context, not a different thread. Anonymous / telephony calls
    get a fresh thread per call (no identity to key on until SIP caller-ID
    resolves, which happens after the session is built).
    """
    account_id = (scope.get("patient") or {}).get("account_id")
    if account_id:
        return f"pt-acc{account_id}-platform"
    return f"voice-{uuid.uuid4()}"


@server.rtc_session(agent_name="appointment-setter")
async def session_handler(ctx: agents.JobContext) -> None:
    graph = await build_graph()
    vad = ctx.proc.userdata.get("vad") or _get_vad()

    scope = await _resolve_channel(ctx)
    if settings.voice_premium_gate:
        scope = await _gate_platform_caller(ctx, scope)
    clinic_id = scope["clinic_id"]
    hospital_id = scope["hospital_id"]
    channel_identifier = scope["identifier"]
    departments = scope.get("departments")
    patient = scope.get("patient")

    session_id = _voice_session_id(scope)

    speech_to_text = build_stt()
    # Local engines (whisper/gemini) are non-streaming → wrap with StreamAdapter +
    # VAD. LiveKit Inference STT is already streaming → use it directly.
    if speech_to_text.capabilities.streaming:
        stt_impl = speech_to_text
    else:
        stt_impl = stt.StreamAdapter(stt=speech_to_text, vad=vad)

    llm = LangGraphLLM(
        graph, session_id, clinic_id, channel_identifier,
        hospital_id=hospital_id,
        doctor_id=scope.get("doctor_id"),
        departments=departments,
        patient=patient,
        room=ctx.room,
        # Any account-holder call joins the account's unified platform-mode
        # thread — the binding must match that thread's prompt head (includes
        # search_doctors) or the KV cache re-prefills every turn. Telephony
        # (no account) keeps the clinic/hospital binding.
        platform=bool(scope.get("platform") or (patient or {}).get("account_id")),
    )

    session_kwargs = dict(
        vad=vad,
        stt=stt_impl,
        llm=llm,
        tts=build_tts(),
    )
    if speech_to_text.capabilities.streaming:
        # Streaming STT means LiveKit Inference — use its turn-detector model for
        # natural Bangla end-of-turn, and tune interruptions so the agent isn't cut
        # off by short back-channels ("হ্যাঁ", "আচ্ছা") yet stays interruptible.
        session_kwargs["turn_detection"] = inference.TurnDetector()
        session_kwargs["min_endpointing_delay"] = 0.5
        session_kwargs["max_endpointing_delay"] = 3.0
        session_kwargs["min_interruption_duration"] = 0.5
        session_kwargs["resume_false_interruption"] = True

    session = AgentSession(**session_kwargs)
    await session.start(
        room=ctx.room,
        agent=DoctorAssistant(hospital_id=hospital_id, langgraph_llm=llm),
        room_input_options=_room_input_options(),
    )

    # Premium gate declined: speak an LLM-composed explanation (never a canned
    # string), SMS the caller an upgrade link (deterministic chrome — the URL
    # never passes through the LLM), and hang up.
    if scope.get("denied"):
        if scope.get("caller"):
            try:
                await send_sms(
                    scope["caller"],
                    "এই নম্বরে কল করা প্রিমিয়াম সদস্যদের জন্য। সাবস্ক্রাইব ও নম্বর "
                    f"যাচাই করুন: {settings.portal_base_url}/portal/account",
                )
            except Exception:
                log.warning("decline SMS failed", exc_info=True)
        await session.generate_reply(
            instructions=(
                "This caller is not recognised as a premium member. Politely explain "
                "in Bangla that voice calling on this number is only for premium "
                "members, that they can subscribe and verify their phone number in "
                "the patient portal (an SMS with the link has just been sent to "
                "them), thank them, and say goodbye. Do not call any tools."
            )
        )
        await _hangup(ctx)
        return

    # IVR-style greeting only for telephony hospital calls (voice_ivr) with no
    # department chosen. Account-holder calls run on the unified platform-mode
    # thread whose prompt never renders the HOSPITAL MODE department-picker —
    # an "ask which department" instruction would contradict it, so they get
    # the generic greeting instead.
    if hospital_id and clinic_id is None and not (patient or {}).get("account_id"):
        await session.generate_reply(
            instructions="Greet the patient in Bangla and ask which department they need."
        )
    else:
        await session.generate_reply(instructions="Greet the patient in Bangla and begin.")


if __name__ == "__main__":
    agents.cli.run_app(server)
