"""Quality-first TTS with an automatic fast fallback.

Wraps a high-quality but slower engine (Indic Parler-TTS) as the primary and a
fast engine (MMS-TTS or espeak-ng) as the fallback. For each chunk it tries the
primary with a wall-clock budget; if synthesis exceeds the budget or raises, it
transparently falls back to the fast engine so a live call never stalls.

The primary runs in a worker thread. On timeout the thread can't be cancelled, so
it finishes in the background (its audio is discarded) while we serve the fast
result — wasted compute, but the caller stays responsive.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Callable

from livekit.agents import tts

log = logging.getLogger(__name__)

# Synth callables take the text and return (pcm16_bytes, sample_rate).
SynthFn = Callable[[str], "tuple[bytes, int]"]


class FallbackTTS(tts.TTS):
    def __init__(
        self,
        primary: SynthFn,
        fast: SynthFn,
        *,
        sample_rate: int,
        timeout_s: float,
    ) -> None:
        self._primary = primary
        self._fast = fast
        self._timeout_s = timeout_s
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=sample_rate,
            num_channels=1,
        )

    def synthesize(self, text: str, *, conn_options=None) -> "_FallbackStream":
        return _FallbackStream(self, text, conn_options=conn_options)


class _FallbackStream(tts.ChunkedStream):
    def __init__(self, tts_: FallbackTTS, text, *, conn_options):
        super().__init__(tts=tts_, input_text=text, conn_options=conn_options)
        self._t = tts_

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        try:
            pcm, sr = await asyncio.wait_for(
                asyncio.to_thread(self._t._primary, self.input_text),
                timeout=self._t._timeout_s,
            )
        except asyncio.TimeoutError:
            log.warning(
                "Primary TTS exceeded %.1fs budget; using fast fallback",
                self._t._timeout_s,
            )
            pcm, sr = await asyncio.to_thread(self._t._fast, self.input_text)
        except Exception:
            log.exception("Primary TTS failed; using fast fallback")
            pcm, sr = await asyncio.to_thread(self._t._fast, self.input_text)

        output_emitter.initialize(
            request_id=str(uuid.uuid4()),
            sample_rate=sr or self._t.sample_rate,
            num_channels=1,
            mime_type="audio/pcm",
        )
        if pcm:
            output_emitter.push(pcm)
        output_emitter.flush()
