"""Doctor profile (description/degrees/photo) — tool output, CRUD API, photo
endpoints, and the doctor-aware prewarm.

The profile exists so (a) patients can pick a doctor from real credentials on
the portal tiles and (b) the agent can answer "which doctor should I see?"
from admin-written facts instead of hallucinating.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver


# ---------------------------------------------------------------------------
# list_doctors tool — degrees + description snippets reach the LLM
# ---------------------------------------------------------------------------

async def test_list_doctors_tool_lines_include_profile(monkeypatch):
    from agent import tools

    async def fake_doctors(clinic_id):
        return [
            {"id": 1, "name": "Rahim", "degrees": "MBBS, FCPS", "specialty": "Cardiology",
             "description": "১৫ বছরের অভিজ্ঞতা।"},
            {"id": 2, "name": "Karim", "degrees": "", "specialty": "",
             "description": ""},
        ]

    monkeypatch.setattr(tools, "_get_doctors_for_clinic", fake_doctors)
    cmd = await tools.list_doctors.coroutine(
        tool_call_id="t1", state={"clinic_id": 2}
    )
    text = cmd.update["messages"][0].content
    assert "1. Rahim, MBBS, FCPS (Cardiology) — ১৫ বছরের অভিজ্ঞতা।" in text
    assert "2. Karim" in text
    # No empty separators for a bare-bones doctor.
    assert "2. Karim," not in text and "Karim —" not in text


async def test_list_doctors_tool_truncates_long_description(monkeypatch):
    from agent import tools

    async def fake_doctors(clinic_id):
        return [{"id": 1, "name": "Rahim", "description": "x" * 500}]

    monkeypatch.setattr(tools, "_get_doctors_for_clinic", fake_doctors)
    cmd = await tools.list_doctors.coroutine(tool_call_id="t1", state={"clinic_id": 2})
    line = cmd.update["messages"][0].content.splitlines()[1]
    assert len(line) < 160  # 120-char snippet + prefix, not the full 500


# ---------------------------------------------------------------------------
# Doctors CRUD + photo endpoints (admin API)
# ---------------------------------------------------------------------------

_DOCTOR_ROW = {
    "id": 5, "clinic_id": 1, "name": "Rahim", "specialty": "Cardiology",
    "degrees": "MBBS", "description": "Bio", "phone": "", "is_primary": True,
    "has_photo": False, "created_at": "2026-01-01T00:00:00Z",
}


@pytest.fixture
async def admin_client():
    from agent.graph import build_graph
    from api.app import app
    from api.deps import current_clinic_id, current_user

    graph = await build_graph(checkpointer=InMemorySaver())
    app.dependency_overrides[current_clinic_id] = lambda: 1
    app.dependency_overrides[current_user] = lambda: {"id": 1, "email": "a@b.c", "role": "admin"}

    with (
        patch("api.app.build_graph", new_callable=AsyncMock, return_value=graph),
        patch("api.app.get_pool", new_callable=AsyncMock, return_value=MagicMock()),
        patch("api.app.close_pool", new_callable=AsyncMock),
        patch("tools.reminders.send_pending_reminders", new_callable=AsyncMock, return_value=0),
        patch("api.routes.doctors.audit_action", new_callable=AsyncMock),
    ):
        with TestClient(app) as c:
            yield c

    app.dependency_overrides.pop(current_clinic_id, None)
    app.dependency_overrides.pop(current_user, None)


def test_create_doctor_passes_profile_fields(admin_client):
    with patch(
        "api.routes.doctors.add_doctor", new_callable=AsyncMock, return_value=_DOCTOR_ROW
    ) as add:
        r = admin_client.post("/doctors", json={
            "name": "Rahim", "degrees": "MBBS", "description": "Bio",
        })
    assert r.status_code == 201
    assert add.call_args.kwargs["degrees"] == "MBBS"
    assert add.call_args.kwargs["description"] == "Bio"
    body = r.json()
    assert body["degrees"] == "MBBS" and body["has_photo"] is False


def test_patch_doctor_updates_description(admin_client):
    row = {**_DOCTOR_ROW, "description": "New bio"}
    with patch(
        "api.routes.doctors.update_doctor", new_callable=AsyncMock, return_value=row
    ) as upd:
        r = admin_client.patch("/doctors/5", json={"description": "New bio"})
    assert r.status_code == 200
    assert upd.call_args.kwargs == {"description": "New bio"}
    assert r.json()["description"] == "New bio"


def test_create_doctor_rejects_oversized_description(admin_client):
    r = admin_client.post("/doctors", json={"name": "R", "description": "x" * 2001})
    assert r.status_code == 422


def test_photo_upload_and_serve(admin_client):
    stored = {}

    async def fake_set(clinic_id, doctor_id, data, mime):
        stored["args"] = (clinic_id, doctor_id, data, mime)
        return True

    with patch("api.routes.doctors.set_doctor_photo", side_effect=fake_set):
        r = admin_client.put(
            "/doctors/5/photo",
            files={"file": ("me.png", b"\x89PNG fake", "image/png")},
        )
    assert r.status_code == 204
    assert stored["args"] == (1, 5, b"\x89PNG fake", "image/png")

    with patch(
        "api.routes.doctors.get_doctor_photo",
        new_callable=AsyncMock, return_value=(b"\x89PNG fake", "image/png"),
    ):
        r = admin_client.get("/doctors/5/photo")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content == b"\x89PNG fake"


def test_photo_upload_rejects_bad_type_and_size(admin_client):
    r = admin_client.put(
        "/doctors/5/photo", files={"file": ("x.gif", b"GIF89a", "image/gif")}
    )
    assert r.status_code == 415

    big = b"0" * (2 * 1024 * 1024 + 1)
    r = admin_client.put(
        "/doctors/5/photo", files={"file": ("x.png", big, "image/png")}
    )
    assert r.status_code == 413


def test_photo_upload_404_for_other_clinics_doctor(admin_client):
    # set_doctor_photo is clinic-scoped; a doctor outside clinic 1 updates 0 rows.
    with patch(
        "api.routes.doctors.set_doctor_photo", new_callable=AsyncMock, return_value=False
    ):
        r = admin_client.put(
            "/doctors/999/photo", files={"file": ("x.png", b"p", "image/png")}
        )
    assert r.status_code == 404


def test_photo_delete_and_missing_photo_404(admin_client):
    with patch(
        "api.routes.doctors.set_doctor_photo", new_callable=AsyncMock, return_value=True
    ) as clear:
        r = admin_client.delete("/doctors/5/photo")
    assert r.status_code == 204
    assert clear.call_args.args == (1, 5, None, None)

    with patch(
        "api.routes.doctors.get_doctor_photo", new_callable=AsyncMock, return_value=None
    ):
        r = admin_client.get("/doctors/5/photo")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Prewarm carries the pre-selected doctor (prompt-cache match)
# ---------------------------------------------------------------------------

@pytest.fixture
async def patient_client():
    from agent.graph import build_graph
    from api.app import app
    from api.deps import current_patient

    graph = await build_graph(checkpointer=InMemorySaver())
    app.dependency_overrides[current_patient] = lambda: {"account_id": 1}

    with (
        patch("api.app.build_graph", new_callable=AsyncMock, return_value=graph),
        patch("api.app.get_pool", new_callable=AsyncMock, return_value=MagicMock()),
        patch("api.app.close_pool", new_callable=AsyncMock),
        patch("tools.reminders.send_pending_reminders", new_callable=AsyncMock, return_value=0),
    ):
        with TestClient(app) as c:
            yield c

    app.dependency_overrides.pop(current_patient, None)


_ACCOUNT = {"id": 1, "name": "kodu", "phone": "01711000000"}


def _prewarm(client, body):
    with (
        patch("api.routes.patient_portal.get_patient_account",
              new_callable=AsyncMock, return_value=_ACCOUNT),
        patch("api.routes.patient_portal.get_hospital_id_for_clinic",
              new_callable=AsyncMock, return_value=7),
        patch("api.routes.patient_portal.get_doctor",
              new_callable=AsyncMock,
              side_effect=lambda did, clinic_id=None: (
                  {"id": did} if did == 5 else None
              )) ,
        patch("agent.nodes.prewarm_turn", new_callable=AsyncMock) as warm,
    ):
        r = client.post("/patient/chat/prewarm", json=body)
    return r, warm


def test_prewarm_seeds_valid_doctor(patient_client):
    r, warm = _prewarm(patient_client, {"clinic_id": 2, "doctor_id": 5})
    assert r.status_code == 200 and r.json() == {"ok": True}
    state = warm.call_args.args[0]
    assert state["doctor_id"] == 5
    assert state["clinic_id"] == 2


def test_prewarm_drops_mismatched_doctor(patient_client):
    r, warm = _prewarm(patient_client, {"clinic_id": 2, "doctor_id": 999})
    assert r.status_code == 200
    assert warm.call_args.args[0]["doctor_id"] is None


def test_prewarm_without_doctor_still_works(patient_client):
    r, warm = _prewarm(patient_client, {"clinic_id": 2})
    assert r.status_code == 200
    assert warm.call_args.args[0]["doctor_id"] is None
