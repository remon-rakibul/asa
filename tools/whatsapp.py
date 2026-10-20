"""Outbound WhatsApp messaging via the Meta Cloud API.

No-op unless WHATSAPP_TOKEN and WHATSAPP_PHONE_ID are set, mirroring tools.sms,
so the agent runs without WhatsApp credentials during development.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from functools import lru_cache

import httpx

from config import settings
from tools import outbox
from tools.outbox import NotConfigured
from utils.text import to_e164_bd

log = logging.getLogger(__name__)

_GRAPH = "https://graph.facebook.com/v21.0"


def _configured() -> bool:
    return bool(settings.whatsapp_token and settings.whatsapp_phone_id)


def _wa_number(to: str) -> str:
    """Meta wants digits with the country code and no '+' (8801XXXXXXXXX).
    Inbound `from` is already in that form; app-stored numbers are 01XXXXXXXXX."""
    e164 = to_e164_bd(to)
    return e164[1:] if e164 else to.lstrip("+")


async def provider_send(to: str, body: str, _sender_id: str = "") -> tuple[str, str | None]:
    """Send one WhatsApp text via the Meta Cloud API; raise on failure.

    Returns ("whatsapp", wamid). Used by tools.outbox, which owns retries and
    metering.
    """
    if not _configured():
        raise NotConfigured("WhatsApp is not configured")
    payload = {
        "messaging_product": "whatsapp",
        "to": _wa_number(to),
        "type": "text",
        "text": {"body": body},
    }
    headers = {"Authorization": f"Bearer {settings.whatsapp_token}"}
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{_GRAPH}/{settings.whatsapp_phone_id}/messages", json=payload, headers=headers,
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"WhatsApp {resp.status_code}: {resp.text[:300]}")
    messages = (resp.json() or {}).get("messages") or [{}]
    return "whatsapp", messages[0].get("id")


async def send_whatsapp_text(to: str, body: str, clinic_id: int | None = None,
                             kind: str = "agent_reply") -> str:
    """Send a plain text WhatsApp message via the outbox (inline attempt +
    retries). Never raises; returns the outbox status. A successful send
    meters one WhatsApp credit against the clinic's hospital wallet (no-op
    when credits are off)."""
    return await outbox.send(
        "whatsapp", to, body, kind=kind, clinic_id=clinic_id, configured=_configured(),
    )


async def download_media(media_id: str) -> bytes | None:
    """Fetch a media object's bytes (two-step: resolve URL, then download)."""
    if not _configured():
        return None
    headers = {"Authorization": f"Bearer {settings.whatsapp_token}"}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            meta = await client.get(f"{_GRAPH}/{media_id}", headers=headers)
            meta.raise_for_status()
            media_url = meta.json()["url"]
            blob = await client.get(media_url, headers=headers)
            blob.raise_for_status()
            return blob.content
    except Exception:
        log.exception("WhatsApp media download failed for %s", media_id)
        return None


# Shown to the agent (not the patient) when a voice note can't be read, so the
# agent composes its own short request to type instead (no canned text).
VOICE_NOTE_UNREADABLE = (
    "[The patient sent a WhatsApp voice message that could not be transcribed. "
    "Briefly ask them to type their message instead.]"
)

_STT_PROMPT = (
    "Transcribe this voice message exactly, in its original language (Bengali / "
    "Bangla, in Bengali script). Output ONLY the verbatim transcript — no quotes, "
    "labels, translation or commentary. If there is no intelligible speech, output nothing."
)


def _voice_note_engine() -> str:
    """'gemini' | 'whisper' | 'off' — resolved from WHATSAPP_VOICE_NOTE_STT.

    auto: Gemini when GEMINI_API_KEY is set (the backend image has no local
    speech model), else local faster-whisper if it is installed, else off.
    Gemini means the patient's recorded voice leaves the box (PII) — set
    WHATSAPP_VOICE_NOTE_STT=whisper to keep it local (needs faster-whisper).
    """
    choice = (settings.whatsapp_voice_note_stt or "auto").lower()
    if choice != "auto":
        return choice
    if settings.gemini_api_key:
        return "gemini"
    try:
        import faster_whisper  # noqa: F401
        return "whisper"
    except ImportError:
        return "off"


async def _gemini_transcribe(audio: bytes, mime_type: str) -> str:
    """Gemini REST generateContent with inline audio (WhatsApp sends OGG/Opus,
    which Gemini accepts directly). Plain HTTP — no SDK in the backend image."""
    import base64

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{settings.gemini_stt_model}:generateContent"
    )
    payload = {"contents": [{"parts": [
        {"text": _STT_PROMPT},
        {"inline_data": {"mime_type": mime_type, "data": base64.b64encode(audio).decode()}},
    ]}]}
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(url, json=payload, headers={"x-goog-api-key": settings.gemini_api_key})
    resp.raise_for_status()
    parts = (((resp.json().get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
    return " ".join(p.get("text", "") for p in parts).strip()


async def transcribe_voice_note(media_id: str, mime_type: str = "audio/ogg") -> str:
    """Download a WhatsApp voice note and transcribe it to Bangla text.

    Returns the transcript, or VOICE_NOTE_UNREADABLE when the audio can't be
    fetched or transcribed — so the patient still gets an (agent-composed)
    answer instead of silence.
    """
    engine = _voice_note_engine()
    if engine == "off":
        log.warning("WhatsApp voice note received but no transcription engine is available")
        return VOICE_NOTE_UNREADABLE
    audio = await download_media(media_id)
    if not audio:
        return VOICE_NOTE_UNREADABLE
    try:
        if engine == "gemini":
            text = await _gemini_transcribe(audio, mime_type.split(";")[0] or "audio/ogg")
        else:
            text = await asyncio.to_thread(_whisper_transcribe, audio)
    except Exception as exc:
        log.warning("Voice note transcription (%s) failed: %s", engine, type(exc).__name__)
        return VOICE_NOTE_UNREADABLE
    return text or VOICE_NOTE_UNREADABLE


@lru_cache(maxsize=1)
def _whisper_model(name: str, device: str):
    from faster_whisper import WhisperModel
    return WhisperModel(name, device=device, compute_type="int8")


def _whisper_transcribe(audio: bytes) -> str:
    model = _whisper_model(settings.whisper_model, settings.whisper_device)
    with tempfile.NamedTemporaryFile(suffix=".ogg") as f:
        f.write(audio)
        f.flush()
        segments, _ = model.transcribe(f.name, language=settings.stt_language)
        return " ".join(seg.text for seg in segments).strip()
