"""Tests for agent/tools.py::request_human_help — the hospital-level fallback
clinic fix (an escalation raised before a department is chosen used to be
written with clinic_id=NULL, which no admin queue query could ever match)."""

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
    assert "ESCALATED" in result.update["messages"][0].content


async def test_escalation_falls_back_to_first_department_when_no_clinic_chosen(monkeypatch):
    create = AsyncMock(return_value=1)
    list_depts = AsyncMock(return_value=[{"id": 9, "name": "Cardiology"}, {"id": 10, "name": "ENT"}])
    monkeypatch.setattr(tools, "_create_escalation", create)
    monkeypatch.setattr(tools, "_list_departments", list_depts)
    state = {"clinic_id": None, "hospital_id": 1, "session_id": "s1", "channel": "voice"}
    await _call(state)
    list_depts.assert_awaited_once_with(1)
    assert create.call_args.kwargs["clinic_id"] == 9


async def test_escalation_stays_none_when_no_hospital_either(monkeypatch):
    create = AsyncMock(return_value=1)
    list_depts = AsyncMock(return_value=[{"id": 9}])
    monkeypatch.setattr(tools, "_create_escalation", create)
    monkeypatch.setattr(tools, "_list_departments", list_depts)
    state = {"clinic_id": None, "hospital_id": None, "session_id": "s1", "channel": "web"}
    await _call(state)
    list_depts.assert_not_awaited()
    assert create.call_args.kwargs["clinic_id"] is None
