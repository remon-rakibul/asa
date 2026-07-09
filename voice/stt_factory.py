"""Pick the configured STT engine, with a safe fallback to local whisper.

settings.stt_engine:
  "whisper" -> local faster-whisper (default; no audio leaves the box).
  "gemini"  -> Google Gemini cloud STT (non-streaming). Sends the patient's audio
               (PII) to Google. Falls back to local whisper on missing key/failure.
  "livekit" -> LiveKit Inference STT (streaming, e.g. speechmatics/enhanced). Needs
               real LiveKit Cloud creds; audio goes via LiveKit. Falls back to local
               whisper when creds are absent or the engine can't be built.

whisper/gemini are non-streaming (main.py wraps them in StreamAdapter + VAD);
the LiveKit engine is streaming and is used directly.
"""

from __future__ import annotations

import logging

from config import settings

log = logging.getLogger(__name__)


def _build_whisper():
    from voice.whisper_stt import WhisperSTT

    return WhisperSTT(
        model=settings.whisper_model,
        language=settings.stt_language,
        device=settings.whisper_device,
    )


def _has_livekit_cloud_creds() -> bool:
    """True only if real (non-default) LiveKit Cloud credentials are configured."""
    return (
        settings.livekit_api_key not in ("", "devkey")
        and settings.livekit_api_secret not in ("", "devsecret")
    )


def build_stt():
    engine = (settings.stt_engine or "whisper").lower()

    if engine == "livekit":
        if _has_livekit_cloud_creds():
            try:
                from livekit.agents import inference

                return inference.STT(
                    model=settings.livekit_stt_model, language=settings.stt_language
                )
            except Exception:
                log.exception("LiveKit Inference STT unavailable; using local whisper")
        else:
            log.warning(
                "STT_ENGINE=livekit but real LIVEKIT_API_KEY/SECRET are not set; "
                "using local whisper"
            )
        return _build_whisper()

    if engine == "gemini":
        if settings.gemini_api_key:
            try:
                from voice.gemini_stt import GeminiSTT

                return GeminiSTT(language=settings.stt_language)
            except Exception:
                log.exception("Gemini STT unavailable; falling back to local whisper")
        else:
            log.warning(
                "STT_ENGINE=gemini but GEMINI_API_KEY is not set; using local whisper"
            )
        return _build_whisper()

    if engine != "whisper":
        log.warning("Unknown STT_ENGINE=%r; using local whisper", engine)
    return _build_whisper()
