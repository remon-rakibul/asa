"""Bangla STT via the Google Gemini API, wrapped as a LiveKit `stt.STT`.

Non-streaming, like WhisperSTT: wrap with `stt.StreamAdapter` + a VAD so the
pipeline feeds it speech segments. Each segment is sent to Gemini as an in-memory
WAV and transcribed.

Unlike whisper_stt this calls an EXTERNAL API, so the patient's recorded audio
(their voice and words — PII) leaves the box. Used only when STT_ENGINE=gemini.
"""

from __future__ import annotations

import asyncio
import io
import wave
from functools import lru_cache

from livekit.agents import stt, utils

from config import settings

_PROMPT = (
    "You are a speech-to-text transcriber. Transcribe the audio exactly, in its "
    "original language (Bengali / Bangla, in Bengali script). Output ONLY the "
    "verbatim transcript text — no quotes, labels, translation, or commentary. "
    "If there is no intelligible speech, output nothing at all."
)


@lru_cache(maxsize=1)
def _client():
    from google import genai

    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY not set")
    return genai.Client(api_key=settings.gemini_api_key)


class GeminiSTT(stt.STT):
    def __init__(self, model: str | None = None, language: str = "bn"):
        super().__init__(
            capabilities=stt.STTCapabilities(streaming=False, interim_results=False)
        )
        self._model = model or settings.gemini_stt_model
        self._language = language
        _client()  # fail fast if the key is missing

    def _transcribe(self, wav_bytes: bytes) -> str:
        from google.genai import types

        resp = _client().models.generate_content(
            model=self._model,
            contents=[
                _PROMPT,
                types.Part.from_bytes(data=wav_bytes, mime_type="audio/wav"),
            ],
        )
        return (resp.text or "").strip()

    async def _recognize_impl(
        self, buffer, *, language=None, conn_options=None
    ) -> stt.SpeechEvent:
        frame = utils.combine_frames(buffer)

        if not frame.data:
            return stt.SpeechEvent(
                type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                alternatives=[stt.SpeechData(language=self._language, text="")],
            )

        wav_buf = io.BytesIO()
        with wave.open(wav_buf, "wb") as wav:
            wav.setnchannels(frame.num_channels)
            wav.setsampwidth(2)  # int16
            wav.setframerate(frame.sample_rate)
            wav.writeframes(bytes(frame.data))

        text = await asyncio.to_thread(self._transcribe, wav_buf.getvalue())

        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[stt.SpeechData(language=self._language, text=text)],
        )
