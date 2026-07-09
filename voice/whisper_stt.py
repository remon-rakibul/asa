"""Local Bangla STT using faster-whisper, wrapped as a LiveKit `stt.STT`.

This is a non-streaming recogniser; wrap it with `stt.StreamAdapter` + a VAD so the
pipeline can feed it speech segments. Audio is handed to faster-whisper as an
in-memory WAV, which it decodes and resamples to 16 kHz internally.
"""

from __future__ import annotations

import asyncio
import io
import wave

from livekit.agents import stt, utils


class WhisperSTT(stt.STT):
    def __init__(self, model: str = "base", language: str = "bn", device: str = "cpu"):
        super().__init__(
            capabilities=stt.STTCapabilities(streaming=False, interim_results=False)
        )
        from faster_whisper import WhisperModel

        # int8 is fast and light on CPU.
        self._model = WhisperModel(model, device=device, compute_type="int8")
        self._language = language

    def _transcribe(self, wav_bytes: bytes) -> str:
        # Greedy decoding (beam_size=1) is much cheaper on CPU. The VAD filter strips
        # silence so Whisper isn't fed near-empty audio (the main cause of ".com .com"
        # style hallucinations), and condition_on_previous_text=False stops one bad
        # turn from poisoning the next. The no-speech / log-prob thresholds drop
        # low-confidence garbage segments entirely.
        segments, _ = self._model.transcribe(
            io.BytesIO(wav_bytes),
            language=self._language,
            beam_size=1,
            vad_filter=True,
            condition_on_previous_text=False,
            no_speech_threshold=0.6,
            log_prob_threshold=-1.0,
        )
        return " ".join(seg.text.strip() for seg in segments).strip()

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
