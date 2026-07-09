"""Two-way reminder replies: ১ confirms, ২ cancels, anything else falls
through to the normal agent (returns None)."""

from __future__ import annotations

import pytest

import tools.reminders as reminders


@pytest.fixture
def appt(monkeypatch):
    row = {"id": "apt-1", "patient_confirmed_at": None}

    async def fake_get(_phone):
        return row

    monkeypatch.setattr(reminders, "get_reminded_appointment_by_phone", fake_get)
    return row


@pytest.mark.asyncio
async def test_confirm_reply_bangla_digit(monkeypatch, appt):
    confirmed: list[str] = []

    async def fake_confirm(aid):
        confirmed.append(aid)

    monkeypatch.setattr(reminders, "confirm_appointment_by_patient", fake_confirm)
    reply = await reminders.handle_reminder_reply("+8801712345678", "১")
    assert reply is not None and "নিশ্চিত" in reply
    assert confirmed == ["apt-1"]


@pytest.mark.asyncio
async def test_cancel_reply(monkeypatch, appt):
    cancelled: list[str] = []

    async def fake_cancel(aid):
        cancelled.append(aid)
        return True

    monkeypatch.setattr(reminders, "cancel_appointment_by_patient", fake_cancel)
    reply = await reminders.handle_reminder_reply("01712345678", "2")
    assert reply is not None and "বাতিল" in reply
    assert cancelled == ["apt-1"]


@pytest.mark.asyncio
async def test_normal_message_falls_through(monkeypatch):
    async def boom(_phone):  # must not even be called for a non-reply message
        raise AssertionError("lookup should not run")

    monkeypatch.setattr(reminders, "get_reminded_appointment_by_phone", boom)
    assert await reminders.handle_reminder_reply("0171", "অ্যাপয়েন্টমেন্ট চাই") is None


@pytest.mark.asyncio
async def test_reply_without_reminded_appointment_falls_through(monkeypatch):
    async def fake_get(_phone):
        return None

    monkeypatch.setattr(reminders, "get_reminded_appointment_by_phone", fake_get)
    assert await reminders.handle_reminder_reply("0171", "১") is None


@pytest.mark.asyncio
async def test_already_confirmed_is_idempotent(monkeypatch):
    async def fake_get(_phone):
        return {"id": "apt-1", "patient_confirmed_at": "2026-07-01T10:00:00Z"}

    monkeypatch.setattr(reminders, "get_reminded_appointment_by_phone", fake_get)
    reply = await reminders.handle_reminder_reply("0171", "1")
    assert reply is not None and "আগেই" in reply
