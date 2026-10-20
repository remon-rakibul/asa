"""Inbound WhatsApp/SMS routing (api/messaging.py) and the messaging turn
wrapper (agent.runner.run_message_turn)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import api.messaging as messaging
from agent import runner


@pytest.mark.asyncio
async def test_hospital_number_mapped_in_channels_keeps_clinic_scope():
    with patch("api.messaging.get_clinic_id_by_channel", AsyncMock(return_value=42)):
        scope = await messaging.resolve_message_scope("whatsapp", "8809611000000", "8801711000000")
    assert scope["session_id"] == "whatsapp-8801711000000"
    assert scope["turn"] == {"clinic_id": 42, "channel": "whatsapp"}


@pytest.mark.asyncio
async def test_asa_number_verified_sender_joins_unified_account_thread():
    account = {"id": 17, "name": "রাকিবুল", "phone": "01711000000"}
    lookup = AsyncMock(return_value=account)
    with (
        patch("api.messaging.get_clinic_id_by_channel", AsyncMock(return_value=None)),
        patch("api.messaging.get_verified_account_by_phone", lookup),
    ):
        scope = await messaging.resolve_message_scope("whatsapp", "8809600000000", "8801711000000")
    lookup.assert_awaited_once_with("01711000000")      # Meta format → stored format
    # Same thread id as the portal chat and voice calls.
    assert scope["session_id"] == "pt-acc17-platform"
    assert scope["turn"]["platform"] is True
    assert scope["turn"]["patient_account_id"] == 17
    assert scope["turn"]["patient_mobile"] == "01711000000"


@pytest.mark.asyncio
async def test_asa_number_unknown_sender_gets_anonymous_platform_thread():
    with (
        patch("api.messaging.get_clinic_id_by_channel", AsyncMock(return_value=None)),
        patch("api.messaging.get_verified_account_by_phone", AsyncMock(return_value=None)),
    ):
        scope = await messaging.resolve_message_scope("sms", "+8809600000000", "+8801811000000")
    assert scope["session_id"] == "sms-+8801811000000"
    assert scope["turn"] == {"channel": "sms", "platform": True, "patient_mobile": "01811000000"}


@pytest.mark.asyncio
async def test_identity_lookup_failure_still_answers_anonymously():
    with (
        patch("api.messaging.get_clinic_id_by_channel", AsyncMock(return_value=None)),
        patch("api.messaging.get_verified_account_by_phone", AsyncMock(side_effect=ConnectionError)),
    ):
        scope = await messaging.resolve_message_scope("whatsapp", "", "8801711000000")
    assert scope["turn"]["platform"] is True and "patient_account_id" not in scope["turn"]


@pytest.mark.asyncio
async def test_platform_mode_off_restores_default_clinic(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "messaging_platform_mode", False)
    with (
        patch("api.messaging.get_clinic_id_by_channel", AsyncMock(return_value=None)),
        patch("api.messaging.get_default_clinic_id", AsyncMock(return_value=1)),
    ):
        scope = await messaging.resolve_message_scope("whatsapp", "x", "8801711000000")
    assert scope["turn"] == {"clinic_id": 1, "channel": "whatsapp"}


@pytest.mark.asyncio
async def test_sender_lock_serialises_turns_for_one_thread():
    order: list[str] = []

    async def turn(tag: str):
        async with messaging.sender_lock("pt-acc1-platform"):
            order.append(f"{tag}-start")
            await asyncio.sleep(0.01)
            order.append(f"{tag}-end")

    await asyncio.gather(turn("a"), turn("b"))
    assert order in (["a-start", "a-end", "b-start", "b-end"],
                     ["b-start", "b-end", "a-start", "a-end"])


def _fake_stream(events):
    async def gen(*_a, **_k):
        for e in events:
            yield e
    return gen


@pytest.mark.asyncio
async def test_message_turn_appends_confirm_question_and_applies_retract():
    events = [
        {"type": "status", "text": "ভাবছি..."},
        {"type": "token", "text": "১. সোমবার ১০টা"},       # fabricated listing...
        {"type": "retract", "keep": 0},                    # ...retracted
        {"type": "token", "text": "আপনার অ্যাপয়েন্টমেন্ট বাতিল করতে চান?"},
        {"type": "confirm", "kind": "cancel", "question": "নিশ্চিত করতে 'হ্যাঁ' লিখুন।"},
        {"type": "end", "appointment_id": None, "done": False},
    ]
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=MagicMock(values={"clinic_id": 9}))
    with patch.object(runner, "stream_turn_tokens", _fake_stream(events)):
        out = await runner.run_message_turn(graph, "pt-acc1-platform", "বাতিল", platform=True)
    # No buttons on WhatsApp/SMS — the confirm question must be in the text.
    assert out["reply"] == "আপনার অ্যাপয়েন্টমেন্ট বাতিল করতে চান?\n\nনিশ্চিত করতে 'হ্যাঁ' লিখুন।"
    assert "সোমবার" not in out["reply"]
    assert out["confirm"] and out["clinic_id"] == 9


@pytest.mark.asyncio
async def test_message_turn_failed_turn_returns_empty_reply():
    graph = MagicMock()
    graph.aget_state = AsyncMock(side_effect=RuntimeError)
    with patch.object(runner, "stream_turn_tokens", _fake_stream([{"type": "status", "text": "…"}])):
        out = await runner.run_message_turn(graph, "s", "hi", clinic_id=3)
    assert out["reply"] == "" and out["clinic_id"] == 3
