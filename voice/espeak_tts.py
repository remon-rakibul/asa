"""Local Bangla TTS via espeak-ng, wrapped as a LiveKit `tts.TTS`.

Piper has no Bengali voice, so we use espeak-ng (`-v bn`). It is robotic but fully
offline and pronounces Bengali. espeak-ng emits a WAV on stdout; we parse it to
raw PCM and push it through the AudioEmitter.
"""

from __future__ import annotations

import asyncio
import io
import subprocess
import uuid
import wave

from livekit.agents import tts


def _synth_pcm(text: str, voice: str, wpm: int) -> tuple[bytes, int]:
    proc = subprocess.run(
        ["espeak-ng", "-v", voice, "-s", str(wpm), "--stdout", text],
        capture_output=True,
        check=False,
    )
    if not proc.stdout:
        return b"", 22050
    with wave.open(io.BytesIO(proc.stdout), "rb") as w:
        return w.readframes(w.getnframes()), w.getframerate()


class EspeakTTS(tts.TTS):
    def __init__(self, voice: str = "bn", words_per_minute: int = 150):
        self._voice = voice
        self._wpm = words_per_minute
        # Probe once to learn the real output sample rate.
        _, sr = _synth_pcm("৯", voice, words_per_minute)
        self._sr = sr or 22050
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=self._sr,
            num_channels=1,
        )

    def synthesize(self, text: str, *, conn_options=None) -> "_EspeakStream":
        return _EspeakStream(
            self, text, voice=self._voice, wpm=self._wpm, conn_options=conn_options
        )


class _EspeakStream(tts.ChunkedStream):
    def __init__(self, tts_, text, *, voice, wpm, conn_options):
        super().__init__(tts=tts_, input_text=text, conn_options=conn_options)
        self._voice = voice
        self._wpm = wpm

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        pcm, sr = await asyncio.to_thread(
            _synth_pcm, self.input_text, self._voice, self._wpm
        )
        output_emitter.initialize(
            request_id=str(uuid.uuid4()),
            sample_rate=sr or self._tts.sample_rate,
            num_channels=1,
            mime_type="audio/pcm",
        )
        if pcm:
            output_emitter.push(pcm)
        output_emitter.flush()
