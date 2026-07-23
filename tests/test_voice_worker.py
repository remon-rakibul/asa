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


# --- unmapped-call fallback (the platform number) ---------------------------

def _patch_no_channel_match():
    """No metadata + a room name matching no channels row (per-caller dispatch
    rooms are named after the CALLER, so the platform number always lands here)."""
    from unittest.mock import AsyncMock, patch
    return (
        patch("main.get_channel_scope", new=AsyncMock(
            return_value={"clinic_id": None, "hospital_id": None, "identifier": None})),
        patch("main.get_channel_by_kind_and_identifier", new=AsyncMock(return_value=None)),
    )


async def test_resolve_channel_unmapped_call_runs_platform_mode():
    import main
    p1, p2 = _patch_no_channel_match()
    with p1, p2:
        scope = await main._resolve_channel(_ctx(room_name="+8801711000000-x7Kq"))
    assert scope["clinic_id"] is None
    assert scope["hospital_id"] is None
    assert scope["platform"] is True
    assert scope["patient"] is None


async def test_resolve_channel_legacy_default_clinic_fallback():
    from unittest.mock import AsyncMock, patch

    import main
    p1, p2 = _patch_no_channel_match()
    with (
        p1, p2,
        patch("main.get_default_clinic_id", new=AsyncMock(return_value=1)),
        patch.object(main.settings, "voice_fallback_scope", "default_clinic"),
    ):
        scope = await main._resolve_channel(_ctx(room_name="+8801711000000-x7Kq"))
    assert scope["clinic_id"] == 1
    assert scope["platform"] is False


async def test_resolve_channel_mapped_did_stays_scoped():
    """A DID explicitly mapped to a clinic keeps its scope — the platform
    fallback only applies to unmapped calls."""
    from unittest.mock import AsyncMock, patch

    import main
    with (
        patch("main.get_channel_scope", new=AsyncMock(
            return_value={"clinic_id": None, "hospital_id": None, "identifier": None})),
        patch("main.get_channel_by_kind_and_identifier", new=AsyncMock(
            return_value={"clinic_id": 3, "identifier": "+8809611000000"})),
    ):
        scope = await main._resolve_channel(_ctx(room_name="+8809611000000"))
    assert scope["clinic_id"] == 3
    assert scope["platform"] is False
    assert scope["identifier"] == "+8809611000000"


# --- premium gate on the platform number -------------------------------------

def _sip_ctx(*, caller_attr=None, room_name="room-x"):
    p = SimpleNamespace(attributes={"sip.phoneNumber": caller_attr} if caller_attr else {})
    return SimpleNamespace(
        job=SimpleNamespace(metadata=None),
        room=SimpleNamespace(name=room_name, remote_participants={"sid": p}),
    )


_PLATFORM_SCOPE = {"clinic_id": None, "hospital_id": None, "platform": True,
                   "identifier": None, "departments": None, "patient": None}


def test_caller_number_prefers_sip_attribute():
    import main
    ctx = _sip_ctx(caller_attr="+8801711000000", room_name="call-+8801799999999-Ab1")
    assert main._caller_number(ctx) == "01711000000"


def test_caller_number_parses_room_name_without_suffix_digits():
    import main
    # Individual dispatch: room named after the caller + random suffix — the
    # suffix's digits must not corrupt the extracted number.
    ctx = _sip_ctx(room_name="call-+8801711000000-x9Kq12")
    assert main._caller_number(ctx) == "01711000000"


def test_caller_number_none_when_hidden():
    import main
    assert main._caller_number(_sip_ctx(room_name="console")) is None


async def test_gate_verified_premium_caller_joins_account_thread():
    from unittest.mock import AsyncMock, patch

    import main
    account = {"id": 7, "name": "Kodu", "phone": "01711000000"}
    with (
        patch("main.get_verified_account_by_phone", new=AsyncMock(return_value=account)),
        patch("main.patient_tier", return_value="premium"),
    ):
        scope = await main._gate_platform_caller(
            _sip_ctx(caller_attr="+8801711000000"), dict(_PLATFORM_SCOPE))
    assert scope.get("denied") is None
    assert scope["patient"]["account_id"] == 7
    # The unified account thread — same as portal chat/voice.
    assert main._voice_session_id(scope) == "pt-acc7-platform"


async def test_gate_declines_free_tier_and_unknown_and_hidden():
    from unittest.mock import AsyncMock, patch

    import main
    # Verified but free tier → declined.
    with (
        patch("main.get_verified_account_by_phone",
              new=AsyncMock(return_value={"id": 7, "phone": "01711000000"})),
        patch("main.patient_tier", return_value="free"),
    ):
        scope = await main._gate_platform_caller(
            _sip_ctx(caller_attr="01711000000"), dict(_PLATFORM_SCOPE))
    assert scope["denied"] is True and scope["caller"] == "01711000000"

    # Unknown number → declined.
    with patch("main.get_verified_account_by_phone", new=AsyncMock(return_value=None)):
        scope = await main._gate_platform_caller(
            _sip_ctx(caller_attr="01799999999"), dict(_PLATFORM_SCOPE))
    assert scope["denied"] is True

    # Hidden caller-ID → declined (no lookup possible).
    scope = await main._gate_platform_caller(
        _sip_ctx(room_name="anonymous"), dict(_PLATFORM_SCOPE))
    assert scope["denied"] is True and scope["caller"] is None


async def test_gate_skips_scoped_and_browser_calls():
    import main
    # Hospital/clinic DID (not platform) — never gated.
    clinic_scope = {**_PLATFORM_SCOPE, "platform": False, "clinic_id": 3}
    assert await main._gate_platform_caller(_sip_ctx(), dict(clinic_scope)) == clinic_scope

    # Browser platform call (patient identity present) — already gated by the
    # 402 on the voice-token endpoint.
    browser_scope = {**_PLATFORM_SCOPE, "patient": {"account_id": 7}}
    assert await main._gate_platform_caller(_sip_ctx(), dict(browser_scope)) == browser_scope
