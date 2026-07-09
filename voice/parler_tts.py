"""High-quality, natural Bangla TTS via AI4Bharat Indic Parler-TTS.

`ai4bharat/indic-parler-tts` is a multilingual Indic fine-tune of Parler-TTS Mini
(~880M params, DAC audio codec). It officially supports Bengali and sounds far more
natural and expressive than the VITS-based `facebook/mms-tts-ben` — the voice,
pace and tone are steered by a plain-text *description* prompt rather than being
fixed. Runs locally on CPU; the model is downloaded once from Hugging Face on
first use (set HF_HOME to control the cache) and then loaded fully offline.

It is heavier than MMS, so synthesis is slower on CPU. The voice worker wraps it
in FallbackTTS (voice/fallback_tts.py), which transparently drops to a fast engine
when a chunk takes too long — keeping live calls responsive.

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

_MODEL_ID = "ai4bharat/indic-parler-tts"

# Sensible default voice for a clinic assistant. AI4Bharat recommends named
# speakers per language; for Bengali "Aditi"/"Arjun" are well-supported. Override
# via TTS_VOICE_DESCRIPTION in .env to change gender/tone/pace.
_DEFAULT_DESCRIPTION = (
    "Aditi speaks in a warm, friendly and natural tone at a moderate pace, "
    "with clear pronunciation and very close-sounding, high quality audio with "
    "no background noise."
)


@lru_cache(maxsize=1)
def _load():
    """Lazily import torch/parler-tts and load the model + tokenizers once."""
    import torch
    from parler_tts import ParlerTTSForConditionalGeneration
    from transformers import AutoTokenizer

    model = ParlerTTSForConditionalGeneration.from_pretrained(_MODEL_ID)
    model.eval()
    # Two tokenizers: one for the spoken transcript, one for the voice description.
    prompt_tokenizer = AutoTokenizer.from_pretrained(_MODEL_ID)
    desc_tokenizer = AutoTokenizer.from_pretrained(
        model.config.text_encoder._name_or_path
    )
    sr = int(model.config.sampling_rate)
    return model, prompt_tokenizer, desc_tokenizer, sr, torch


def sample_rate() -> int:
    return _load()[3]


def _synth_pcm(text: str, description: str | None = None) -> tuple[bytes, int]:
    model, prompt_tokenizer, desc_tokenizer, sr, torch = _load()
    description = description or settings.tts_voice_description or _DEFAULT_DESCRIPTION

    desc = desc_tokenizer(description, return_tensors="pt")
    prompt = prompt_tokenizer(text, return_tensors="pt")
    with torch.no_grad():
        generation = model.generate(
            input_ids=desc.input_ids,
            attention_mask=desc.attention_mask,
            prompt_input_ids=prompt.input_ids,
            prompt_attention_mask=prompt.attention_mask,
        )
    audio = generation.cpu().numpy().squeeze().astype(np.float32)
    pcm16 = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
    return pcm16, sr


class ParlerTTS(tts.TTS):
    def __init__(self) -> None:
        self._sr = sample_rate()  # warms the model cache
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=self._sr,
            num_channels=1,
        )

    def synthesize(self, text: str, *, conn_options=None) -> "_ParlerStream":
        return _ParlerStream(self, text, conn_options=conn_options)


class _ParlerStream(tts.ChunkedStream):
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
