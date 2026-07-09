"""High-quality multilingual TTS via the Google Gemini API.

`gemini-2.5-flash-preview-tts` produces natural, expressive speech and supports
Bengali (the language is auto-detected from the text). Unlike the local engines
this calls an EXTERNAL API, so the agent's outgoing text — which may contain
patient PII (names, appointment details) — leaves the box. It is wrapped in
FallbackTTS (voice/fallback_tts.py) so a slow network or API error transparently
drops to the local MMS/espeak engine, keeping the fully-local path as a safety net.

Output is raw 16-bit PCM, mono, 24 kHz.

Fed the sentence/clause chunks produced by the streaming runner, so synthesis
overlaps LLM generation. Non-streaming per chunk (LiveKit's StreamAdapter
segments upstream).
"""

from __future__ import annotations

import asyncio
import uuid
from functools import lru_cache

from livekit.agents import tts

from config import settings

_DEFAULT_MODEL = "gemini-2.5-flash-preview-tts"
_DEFAULT_VOICE = "Kore"  # neutral; ~30 prebuilt voices, language-agnostic
_SAMPLE_RATE = 24000     # Gemini TTS always returns 24 kHz PCM


@lru_cache(maxsize=1)
def _client():
    """Lazily build the Gemini client (raises if no API key is configured)."""
    from google import genai

    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY not set")
    return genai.Client(api_key=settings.gemini_api_key)


def sample_rate() -> int:
    # Constant for Gemini TTS — returned without a network call so the worker can
    # start (and the factory can size buffers) even while offline.
    return _SAMPLE_RATE


def _parse_rate(mime_type: str | None) -> int:
    """Extract the sample rate from a mime like 'audio/L16;codec=pcm;rate=24000'."""
    if mime_type:
        for part in mime_type.split(";"):
            part = part.strip()
            if part.startswith("rate="):
                try:
                    return int(part[len("rate="):])
                except ValueError:
                    pass
    return _SAMPLE_RATE


def _synth_pcm(text: str) -> tuple[bytes, int]:
    from google.genai import types

    client = _client()
    resp = client.models.generate_content(
        model=settings.gemini_tts_model or _DEFAULT_MODEL,
        contents=text,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=settings.gemini_tts_voice or _DEFAULT_VOICE
                    )
                )
            ),
        ),
    )
    inline = resp.candidates[0].content.parts[0].inline_data
    return inline.data or b"", _parse_rate(inline.mime_type)


class GeminiTTS(tts.TTS):
    def __init__(self) -> None:
        self._sr = _SAMPLE_RATE
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=self._sr,
            num_channels=1,
        )

    def synthesize(self, text: str, *, conn_options=None) -> "_GeminiStream":
        return _GeminiStream(self, text, conn_options=conn_options)


class _GeminiStream(tts.ChunkedStream):
    def __init__(self, tts_, text, *, conn_options):
        super().__init__(tts=tts_, input_text=text, conn_options=conn_options)

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        pcm, sr = await asyncio.to_thread(_synth_pcm, self.input_text)
        output_emitter.initialize(
            request_id=str(uuid.uuid4()),
            sample_rate=sr or self._tts.sample_rate,
            num_channels=1,
            mime_type="audio/pcm",
        )
        if pcm:
            output_emitter.push(pcm)
        output_emitter.flush()
