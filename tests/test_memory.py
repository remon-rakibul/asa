"""agent/memory.py — shared cross-session memory writer (agent + direct booking)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from agent import memory


class FakeStore:
    """In-memory stand-in for the LangGraph PostgresStore (aget/aput only)."""

    def __init__(self):
        self.data: dict[tuple, dict] = {}

    async def aget(self, namespace, key):
        value = self.data.get((namespace, key))
        if value is None:
            return None
        item = type("Item", (), {})()
        item.value = value
        return item

    async def aput(self, namespace, key, value):
        self.data[(namespace, key)] = dict(value)


NS = ("patient_memory", "7")


async def test_profile_merges_and_counts_visits():
    store = FakeStore()
    await memory.write_patient_memory(
        store, account_id=7, name="Kodu", age=30, phone="01711000000",
        appointment_id="apt-1", visit={"summary": "ভিজিট ১"},
    )
    profile = store.data[(NS, "profile")]
    assert profile == {"name": "Kodu", "age": 30, "phone": "01711000000", "visit_count": 1}
    assert store.data[(NS, "visit:apt-1")] == {"summary": "ভিজিট ১"}

    # Second visit: new values merge on top, count increments.
    await memory.write_patient_memory(
        store, account_id=7, age=31, appointment_id="apt-2", visit={"summary": "ভিজিট ২"},
    )
    profile = store.data[(NS, "profile")]
    assert profile["name"] == "Kodu"  # kept from before
    assert profile["age"] == 31
    assert profile["visit_count"] == 2


async def test_rewriting_same_appointment_does_not_double_count():
    store = FakeStore()
    for _ in range(2):
        await memory.write_patient_memory(
            store, account_id=7, name="Kodu",
            appointment_id="apt-1", visit={"summary": "ভিজিট"},
        )
    assert store.data[(NS, "profile")]["visit_count"] == 1


async def test_noop_without_store_or_account():
    await memory.write_patient_memory(None, account_id=7, name="X")  # no store
    store = FakeStore()
    await memory.write_patient_memory(store, account_id=0, name="X")  # no account
    assert store.data == {}


async def test_build_visit_record_enriched(monkeypatch):
    monkeypatch.setattr(memory, "get_clinic", AsyncMock(
        return_value={"id": 2, "name": "কার্ডিওলজি", "doctor_name": "Default Doc"}
    ))
    monkeypatch.setattr(memory, "get_hospital_id_for_clinic", AsyncMock(return_value=1))
    monkeypatch.setattr(memory, "get_hospital", AsyncMock(
        return_value={"id": 1, "name": "City Hospital"}
    ))
    monkeypatch.setattr(memory, "get_doctor", AsyncMock(
        return_value={"id": 5, "name": "Rahim", "specialty": "Cardiology"}
    ))

    visit = await memory.build_visit_record(
        clinic_id=2, doctor_id=5, slot_label="সোমবার সকাল ৯টা", serial_number=3,
    )
    # The CHOSEN doctor wins over the clinic's default doctor.
    assert visit["doctor_name"] == "Rahim"
    assert visit["specialty"] == "Cardiology"
    assert visit["hospital_name"] == "City Hospital"
    assert visit["department_name"] == "কার্ডিওলজি"
    assert visit["serial_number"] == 3
    assert "Rahim" in visit["summary"] and "City Hospital" in visit["summary"]


async def test_build_visit_record_degrades_on_db_failure(monkeypatch):
    monkeypatch.setattr(memory, "get_clinic", AsyncMock(side_effect=RuntimeError("db")))
    visit = await memory.build_visit_record(clinic_id=2, doctor_id=5, slot_label="সোমবার")
    assert visit["clinic_id"] == 2 and visit["slot_label"] == "সোমবার"
    assert visit["summary"] == "ভিজিট: সোমবার"
