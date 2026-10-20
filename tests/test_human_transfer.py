"""request_human_help: live transfer to hospital reception on phone calls."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from agent import tools as agent_tools


async def _call(state: dict):
    writes: list[dict] = []
    with (
        patch.object(agent_tools, "_create_escalation", AsyncMock()),
        patch.object(agent_tools, "_get_reception_phone", AsyncMock(return_value="02-9660000")),
        patch.object(agent_tools, "get_stream_writer", lambda: writes.append),
    ):
        cmd = await agent_tools.request_human_help.coroutine(
            reason="wants a person", tool_call_id="tc1", state=state,
        )
    return cmd.update["messages"][0].content, writes


@pytest.mark.asyncio
async def test_phone_call_transfers_to_reception_landline():
    content, writes = await _call({"telephony": True, "clinic_id": 3, "session_id": "s"})
    assert content.startswith("TRANSFERRING")
    assert writes == [{"type": "transfer", "to": "+88029660000"}]     # landline → E.164


@pytest.mark.asyncio
async def test_web_chat_and_browser_voice_only_escalate():
    content, writes = await _call({"telephony": False, "clinic_id": 3, "session_id": "s"})
    assert content.startswith("ESCALATED") and writes == []


def test_to_e164_bd_any():
    from utils.text import to_e164_bd_any

    assert to_e164_bd_any("01712345678") == "+8801712345678"
    assert to_e164_bd_any("02-9660000") == "+88029660000"
    assert to_e164_bd_any("+880 9666 787878") == "+8809666787878"
    assert to_e164_bd_any("8809666787878") == "+8809666787878"
    assert to_e164_bd_any("") == "" and to_e164_bd_any("12345") == ""
