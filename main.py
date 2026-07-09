"""LiveKit voice worker for the Bangla appointment-setter agent.

Pipeline:  Silero VAD -> faster-whisper STT -> LangGraph agent -> neural TTS
           (Indic Parler-TTS, with a fast fallback).

Run modes (livekit-agents CLI):
  python main.py console   # talk via your laptop mic/speakers (no LiveKit server)
  python main.py dev       # connect to a LiveKit room (hot reload)
  python main.py start     # production

Telephony: inbound phone calls arrive via a SIP trunk terminated into LiveKit
SIP (see docs/TELEPHONY.md). The dialed number (DID) maps to a clinic via the
'voice_sip' channel; unmapped calls fall back to the default clinic.

Requires the FastAPI/agent deps plus: faster-whisper, espeak-ng (system pkg),
and (for the default TTS) torch + transformers + parler-tts.
Postgres + Ollama must be running (same as the FastAPI backend).
"""

from __future__ import annotations

import json
import logging
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
    list_departments,
)
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
    Falls back to the default clinic if nothing matches.

    Returns a scope dict with keys: clinic_id, hospital_id, identifier, departments,
    patient. For voice_ivr / hospital-level browser calls, hospital_id is set and
    clinic_id is None. ``patient`` is a dict (browser portal) or None (telephony).
    """
    meta = _parse_job_metadata(ctx)
    if meta and (meta.get("clinic_id") or meta.get("hospital_id")):
        clinic_id = meta.get("clinic_id")
        hospital_id = meta.get("hospital_id")
        # Hospital-level (no chosen department) browser call → load departments so
        # the agent can run the same IVR-style "which department?" greeting.
        departments = None
        if clinic_id is None and hospital_id:
            departments = await list_departments(hospital_id)
        return {
            "clinic_id": clinic_id,
            "hospital_id": hospital_id,
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
            return {**scope, "departments": departments, "patient": None}

        # Then clinic-level voice channels
        for kind in ("voice_sip", "voice"):
            ch = await get_channel_by_kind_and_identifier(kind, dialed)
            if ch is not None:
                return {
                    "clinic_id": ch["clinic_id"],
                    "hospital_id": None,
                    "identifier": ch["identifier"],
                    "departments": None,
                    "patient": None,
                }

    cid = await get_default_clinic_id()
    return {
        "clinic_id": cid, "hospital_id": None, "identifier": None,
        "departments": None, "patient": None,
    }


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

    A logged-in portal patient gets the SAME thread as their web chat
    (pt-acc…, matching lib/api.ts stableSessionId), so a call continues the
    chat's context and the call transcript shows up in the chat history
    afterwards. Anonymous / telephony calls get a fresh thread per call (no
    identity to key on until SIP caller-ID resolves, which happens after the
    session is built).
    """
    account_id = (scope.get("patient") or {}).get("account_id")
    if account_id and scope.get("clinic_id"):
        return f"pt-acc{account_id}-clinic{scope['clinic_id']}"
    if account_id and scope.get("hospital_id"):
        return f"pt-acc{account_id}-hosp{scope['hospital_id']}"
    return f"voice-{uuid.uuid4()}"


@server.rtc_session(agent_name="appointment-setter")
async def session_handler(ctx: agents.JobContext) -> None:
    graph = await build_graph()
    vad = ctx.proc.userdata.get("vad") or _get_vad()

    scope = await _resolve_channel(ctx)
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

    # IVR-style greeting only when no specific department is chosen yet
    # (telephony voice_ivr, or a hospital-level browser call). A department-level
    # browser call has both clinic_id and hospital_id set → greet and begin.
    if hospital_id and clinic_id is None:
        await session.generate_reply(
            instructions="Greet the patient in Bangla and ask which department they need."
        )
    else:
        await session.generate_reply(instructions="Greet the patient in Bangla and begin.")


if __name__ == "__main__":
    agents.cli.run_app(server)
