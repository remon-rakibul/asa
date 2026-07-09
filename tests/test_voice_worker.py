"""Tests for main.py voice-worker channel resolution from dispatch metadata.

Skipped where the optional voice deps (livekit-agents / silero) aren't installed.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

pytest.importorskip("livekit.agents")
pytest.importorskip("livekit.plugins.silero")


def _ctx(*, metadata=None, room_name=None):
    """Minimal stand-in for agents.JobContext (only .job.metadata and .room.name)."""
    return SimpleNamespace(
        job=SimpleNamespace(metadata=metadata),
        room=SimpleNamespace(name=room_name),
    )


def test_parse_job_metadata_valid():
    import main
    assert main._parse_job_metadata(_ctx(metadata=json.dumps({"clinic_id": 3}))) == {"clinic_id": 3}


def test_parse_job_metadata_invalid_or_missing():
    import main
    assert main._parse_job_metadata(_ctx(metadata="not-json")) is None
    assert main._parse_job_metadata(_ctx(metadata=None)) is None
    # A JSON array is not a metadata dict.
    assert main._parse_job_metadata(_ctx(metadata="[1, 2]")) is None


async def test_resolve_channel_prefers_metadata_over_did():
    import main
    ctx = _ctx(
        metadata=json.dumps({
            "clinic_id": 3, "hospital_id": 7,
            "patient_account_id": 1, "patient_id": 99,
            "patient_name": "রাহেলা", "patient_phone": "01711000000",
        }),
        room_name="should-be-ignored-DID",
    )
    scope = await main._resolve_channel(ctx)

    assert scope["clinic_id"] == 3
    assert scope["hospital_id"] == 7
    assert scope["identifier"] is None
    # Department-level call → no IVR department list.
    assert scope["departments"] is None
    assert scope["patient"] == {
        "account_id": 1, "patient_id": 99, "name": "রাহেলা", "phone": "01711000000",
    }


async def test_resolve_channel_carries_preselected_doctor():
    import main
    ctx = _ctx(metadata=json.dumps({
        "clinic_id": 3, "hospital_id": 7, "doctor_id": 5, "patient_account_id": 1,
    }))
    scope = await main._resolve_channel(ctx)
    assert scope["doctor_id"] == 5

    # Telephony scopes never set the key — .get() must yield None downstream.
    ctx = _ctx(metadata=json.dumps({"clinic_id": 3, "patient_account_id": 1}))
    scope = await main._resolve_channel(ctx)
    assert scope.get("doctor_id") is None


async def test_resolve_channel_hospital_level_loads_departments():
    import main
    ctx = _ctx(metadata=json.dumps({"hospital_id": 7, "patient_account_id": 1}))
    with patch_list_departments(["Cardiology"]):
        scope = await main._resolve_channel(ctx)
    assert scope["clinic_id"] is None
    assert scope["hospital_id"] == 7
    assert scope["departments"] == ["Cardiology"]


def patch_list_departments(value):
    from unittest.mock import AsyncMock, patch
    return patch("main.list_departments", new=AsyncMock(return_value=value))
