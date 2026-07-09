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

log = logging.getLogger(__name__)

_GRAPH = "https://graph.facebook.com/v21.0"


def _configured() -> bool:
    return bool(settings.whatsapp_token and settings.whatsapp_phone_id)


async def send_whatsapp_text(to: str, body: str) -> None:
    """Send a plain text WhatsApp message. Errors are logged, never raised."""
    if not _configured():
        return
    url = f"{_GRAPH}/{settings.whatsapp_phone_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": body},
    }
    headers = {"Authorization": f"Bearer {settings.whatsapp_token}"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code >= 400:
                log.error("WhatsApp send failed %s: %s", resp.status_code, resp.text)
    except Exception:
        log.exception("WhatsApp send to %s failed", to)


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


async def transcribe_voice_note(media_id: str) -> str:
    """Download a WhatsApp voice note and transcribe it to Bangla text.

    Best-effort: returns "" if media/transcription is unavailable. Uses the same
    local faster-whisper model as the voice worker (no cloud)."""
    audio = await download_media(media_id)
    if not audio:
        return ""
    try:
        return await asyncio.to_thread(_whisper_transcribe, audio)
    except Exception:
        log.exception("Voice note transcription failed")
        return ""


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
