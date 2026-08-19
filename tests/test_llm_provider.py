"""LLM provider selection + cloud-LLM resilience settings.

conftest pins settings.llm_provider="ollama" for determinism, so these tests set
the provider explicitly and clear the _build_chat_model cache themselves.
"""

from __future__ import annotations

import pytest

from config import settings
from agent import nodes


def test_openrouter_branch_applies_resilience_settings():
    """The OpenRouter client must carry an explicit timeout + retry budget so a
    transient DNS/connection blip (voice call pegs the CPU → embedded DNS drops)
    doesn't kill the turn on the first failure."""
    pytest.importorskip("langchain_openai")
    old_provider = settings.llm_provider
    old_key = settings.openrouter_api_key
    try:
        settings.llm_provider = "openrouter"
        settings.openrouter_api_key = "sk-test-key"
        settings.openrouter_max_retries = 5
        settings.openrouter_timeout_seconds = 60.0
        model = nodes._build_chat_model()
    finally:
        settings.llm_provider = old_provider
        settings.openrouter_api_key = old_key

    assert model.__class__.__name__ == "ChatOpenAI"
    assert model.max_retries == 5
    assert model.request_timeout == 60.0


def test_openrouter_without_key_falls_back_to_ollama():
    old_provider = settings.llm_provider
    old_key = settings.openrouter_api_key
    try:
        settings.llm_provider = "openrouter"
        settings.openrouter_api_key = ""
        model = nodes._build_chat_model()
    finally:
        settings.llm_provider = old_provider
        settings.openrouter_api_key = old_key

    # No key → the branch logs a warning and drops through to local Ollama.
    assert model.__class__.__name__ == "ChatOllama"
