"""Tests for agent/tools.py::request_human_help — routes to wherever the
patient is currently engaging (clinic when chosen, else hospital, else a
pure platform-level escalation) instead of guessing a department."""

from unittest.mock import AsyncMock

import pytest

from agent import tools


async def _call(state, reason="সাহায্য দরকার"):
    return await tools.request_human_help.coroutine(
        reason, tool_call_id="call-1", state=state,
    )


async def test_escalation_uses_clinic_id_when_already_chosen(monkeypatch):
    create = AsyncMock(return_value=1)
    monkeypatch.setattr(tools, "_create_escalation", create)
    state = {"clinic_id": 6, "hospital_id": 1, "session_id": "s1", "channel": "web"}
    result = await _call(state)
    assert create.call_args.kwargs["clinic_id"] == 6
    assert create.call_args.kwargs["hospital_id"] == 1
    assert "ESCALATED" in result.update["messages"][0].content


async def test_escalation_uses_hospital_id_when_no_clinic_chosen(monkeypatch):
    create = AsyncMock(return_value=1)
    monkeypatch.setattr(tools, "_create_escalation", create)
    state = {"clinic_id": None, "hospital_id": 1, "session_id": "s1", "channel": "voice"}
    await _call(state)
    assert create.call_args.kwargs["clinic_id"] is None
    assert create.call_args.kwargs["hospital_id"] == 1


async def test_escalation_is_platform_level_when_no_hospital_either(monkeypatch):
    create = AsyncMock(return_value=1)
    monkeypatch.setattr(tools, "_create_escalation", create)
    state = {"clinic_id": None, "hospital_id": None, "session_id": "s1", "channel": "web"}
    await _call(state)
    assert create.call_args.kwargs["clinic_id"] is None
    assert create.call_args.kwargs["hospital_id"] is None
