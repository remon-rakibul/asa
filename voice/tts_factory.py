"""Pick the configured local TTS engine, with safe fallbacks.

settings.tts_engine:
  "gemini" -> Google Gemini cloud TTS (natural, multilingual) as primary, with a
              local fast fallback when the network/API is slow or down. Sends the
              agent's outgoing text (may contain patient PII) to Google.
  "mms"    -> local neural Bangla (facebook/mms-tts-ben, VITS), fast on CPU.
  "espeak" -> robotic espeak-ng, zero download.
  "parler" -> ai4bharat/indic-parler-tts: very natural but far too slow on CPU
              (autoregressive ~880M); kept for GPU boxes only.

settings.tts_fast_engine ("mms" | "espeak") selects the fast fallback used under
the "gemini" and "parler" engines. If a heavier engine can't load (missing deps
or no network), we degrade gracefully rather than failing the worker.
"""

from __future__ import annotations

import logging

from config import settings

log = logging.getLogger(__name__)


def _espeak():
    from voice.espeak_tts import EspeakTTS, _synth_pcm

    EspeakTTS()  # validates espeak-ng is installed / probes sample rate
    return lambda text: _synth_pcm(text, "bn", 150)


def _mms_synth():
    """Fast-engine synth callable, falling back to espeak if MMS can't load."""
    try:
        from voice.mms_tts import _synth_pcm

        _synth_pcm("হ্যাঁ")  # warm + verify torch/transformers + model download
        return _synth_pcm
    except Exception:
        log.exception("MMS-TTS unavailable for fast fallback; using espeak-ng")
        return _espeak()


def _fast_synth():
    if (settings.tts_fast_engine or "mms").lower() == "espeak":
        return _espeak()
    return _mms_synth()


def _build_parler():
    """Parler primary + fast fallback. Degrade to the fast engine on failure."""
    from voice.fallback_tts import FallbackTTS
    from voice.parler_tts import sample_rate, _synth_pcm as parler_synth

    fast = _fast_synth()
    sr = sample_rate()  # warms/loads the Parler model (raises if unavailable)
    return FallbackTTS(
        primary=parler_synth,
        fast=fast,
        sample_rate=sr,
        timeout_s=settings.tts_quality_timeout_seconds,
    )


def _build_gemini():
    """Gemini cloud TTS primary + local fast fallback (network/API failures)."""
    from voice.fallback_tts import FallbackTTS
    from voice.gemini_tts import sample_rate, _synth_pcm as gemini_synth

    fast = _fast_synth()
    sr = sample_rate()  # constant; no network call
    return FallbackTTS(
        primary=gemini_synth,
        fast=fast,
        sample_rate=sr,
        timeout_s=settings.tts_quality_timeout_seconds,
    )


def _mms_or_espeak():
    """Local engine: prefer MMS-TTS, degrade to espeak-ng if it can't load."""
    try:
        from voice.mms_tts import MmsTTS

        return MmsTTS()
    except Exception:
        log.exception("MMS-TTS unavailable; falling back to espeak-ng")
        from voice.espeak_tts import EspeakTTS

        return EspeakTTS()


def build_tts():
    engine = (settings.tts_engine or "parler").lower()

    if engine == "livekit":
        from voice.stt_factory import _has_livekit_cloud_creds

        if _has_livekit_cloud_creds():
            try:
                from livekit.agents import inference

                kwargs = {
                    "model": settings.livekit_tts_model,
                    "language": settings.stt_language,
                }
                if settings.livekit_tts_voice:
                    kwargs["voice"] = settings.livekit_tts_voice
                return inference.TTS(**kwargs)
            except Exception:
                log.exception("LiveKit Inference TTS unavailable; falling back to MMS")
        else:
            log.warning(
                "TTS_ENGINE=livekit but real LIVEKIT_API_KEY/SECRET are not set; "
                "using local MMS-TTS"
            )
        return _mms_or_espeak()

    if engine == "espeak":
        from voice.espeak_tts import EspeakTTS

        return EspeakTTS()

    if engine == "mms":
        return _mms_or_espeak()

    if engine == "gemini":
        if settings.gemini_api_key:
            try:
                return _build_gemini()
            except Exception:
                log.exception("Gemini TTS unavailable; falling back to MMS-TTS")
        else:
            log.warning(
                "TTS_ENGINE=gemini but GEMINI_API_KEY is not set; using local MMS-TTS"
            )
        return _mms_or_espeak()

    if engine == "parler":
        try:
            return _build_parler()
        except Exception:
            log.exception("Indic Parler-TTS unavailable; falling back to MMS-TTS")
        return _mms_or_espeak()

    # Unknown engine name — default to the local neural engine.
    log.warning("Unknown TTS_ENGINE=%r; using local MMS-TTS", engine)
    return _mms_or_espeak()
