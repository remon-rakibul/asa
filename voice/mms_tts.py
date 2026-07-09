"""High-quality local Bangla TTS via Meta MMS-TTS (facebook/mms-tts-ben).

VITS-based, ~100-200 MB, runs locally on CPU and sounds far more natural than
espeak-ng. Wrapped as a LiveKit `tts.TTS`. The model is downloaded once from
Hugging Face on first use (set HF_HOME to control the cache) and then loaded
fully offline.

Fed the sentence/clause chunks produced by the streaming runner, so synthesis
overlaps LLM generation. Non-streaming per chunk (LiveKit's StreamAdapter
segments upstream).
"""

from __future__ import annotations

import asyncio
import uuid
from functools import lru_cache

import numpy as np
from livekit.agents import tts

from config import settings

_MODEL_ID = "facebook/mms-tts-ben"


def _maybe_shift_pitch(audio: np.ndarray, sr: int) -> np.ndarray:
    """Lower/raise pitch by settings.tts_pitch_semitones, keeping tempo intact."""
    steps = settings.tts_pitch_semitones
    if not steps:
        return audio
    import librosa

    return librosa.effects.pitch_shift(
        audio.astype(np.float32), sr=sr, n_steps=float(steps)
    )


@lru_cache(maxsize=1)
def _load():
    """Lazily import torch/transformers and load the model once."""
    import torch
    from transformers import VitsModel, AutoTokenizer

    model = VitsModel.from_pretrained(_MODEL_ID)
    model.eval()
    tokenizer = AutoTokenizer.from_pretrained(_MODEL_ID)
    return model, tokenizer, int(model.config.sampling_rate), torch


def _synth_pcm(text: str) -> tuple[bytes, int]:
    model, tokenizer, sr, torch = _load()
    inputs = tokenizer(text, return_tensors="pt")
    # MMS-TTS is character-level: glyphs outside its vocab (digits, emoji, most
    # punctuation) are silently dropped. A chunk that's entirely unsupported
    # tokenizes to an empty sequence, which crashes VITS relative-position
    # attention ("narrow(): length must be non-negative"). Emit silence instead.
    if inputs["input_ids"].shape[-1] == 0:
        return b"", sr
    with torch.no_grad():
        waveform = model(**inputs).waveform  # float32 [-1, 1], shape (1, n)
    audio = waveform.squeeze().cpu().numpy()
    audio = _maybe_shift_pitch(audio, sr)
    pcm16 = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
    return pcm16, sr


class MmsTTS(tts.TTS):
    def __init__(self) -> None:
        # Resolve the sample rate up front (also warms the model cache).
        # Use a real Bangla word — a bare digit tokenizes to nothing (see above).
        _, sr = _synth_pcm("হ্যাঁ")
        self._sr = sr
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=self._sr,
            num_channels=1,
        )

    def synthesize(self, text: str, *, conn_options=None) -> "_MmsStream":
        return _MmsStream(self, text, conn_options=conn_options)


class _MmsStream(tts.ChunkedStream):
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
