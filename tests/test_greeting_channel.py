"""Tests for call_model_node clinic branding in the system prompt."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

import agent.nodes as nodes


def _cfg(**kw):
    base = {"name": "Test Clinic", "doctor_name": "Dr. Test"}
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_system_prompt_includes_clinic_name():
    """call_model_node builds a system prompt with the clinic's branding."""
    nodes._clinic_cache.clear()
    captured_messages = []

    async def fake_invoke(messages, **_):
        captured_messages.extend(messages)
        return AIMessage(content="আস-সালামু আলাইকুম!")

    fake_llm = MagicMock()
    fake_llm.ainvoke = fake_invoke

    with (
        patch.object(nodes, "get_clinic", new=AsyncMock(return_value=_cfg(name="আল-শেফা ক্লিনিক"))),
        patch.object(nodes, "_llm_for", return_value=fake_llm),
    ):
        await nodes.call_model_node({"clinic_id": 1, "messages": []})

    system_text = captured_messages[0].content
    assert "আল-শেফা ক্লিনিক" in system_text


@pytest.mark.asyncio
async def test_system_prompt_includes_doctor_name():
    nodes._clinic_cache.clear()
    captured_messages = []

    async def fake_invoke(messages, **_):
        captured_messages.extend(messages)
        return AIMessage(content="ঠিক আছে।")

    fake_llm = MagicMock()
    fake_llm.ainvoke = fake_invoke

    with (
        patch.object(nodes, "get_clinic", new=AsyncMock(return_value=_cfg(doctor_name="ডাঃ রহিম"))),
        patch.object(nodes, "_llm_for", return_value=fake_llm),
    ):
        await nodes.call_model_node({"clinic_id": 1, "messages": []})

    system_text = captured_messages[0].content
    assert "ডাঃ রহিম" in system_text
