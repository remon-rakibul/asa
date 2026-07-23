"""Direct portal booking — POST /patient/appointments (no agent).

Covers the safety rails mirrored from the agent tool (slot membership,
mobile normalization), the identity policy (MRN record from the ACCOUNT,
appointment row from the form), audit attribution (actor_role="patient",
session_id=None), best-effort SMS + memory, and the 404/409/422 edges.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

_ACCOUNT = {"id": 7, "name": "Kodu", "phone": "01711000000"}

_SLOTS = [
    {"datetime": "2026-07-13T09:00:00", "label": "সোমবার সকাল ৯টা"},
    {"datetime": "2026-07-13T09:30:00", "label": "সোমবার সকাল সাড়ে ৯টা"},
]

_BODY = {
    "clinic_id": 2,
    "doctor_id": 5,
    "slot_datetime": "2026-07-13T09:00:00",
    "slot_label": "সোমবার সকাল ৯টা",
    "patient_name": "Kodu",
    "patient_age": 30,
    "patient_mobile": "01711-000000",
}


@pytest.fixture
async def client():
    from agent.graph import build_graph
    from api.app import app
    from api.deps import current_patient

    graph = await build_graph(checkpointer=InMemorySaver())
    app.dependency_overrides[current_patient] = lambda: {"account_id": 7}

    with (
        patch("api.app.build_graph", new_callable=AsyncMock, return_value=graph),
        patch("api.app.get_pool", new_callable=AsyncMock, return_value=MagicMock()),
        patch("api.app.close_pool", new_callable=AsyncMock),
        patch("tools.reminders.send_pending_reminders", new_callable=AsyncMock, return_value=0),
    ):
        with TestClient(app) as c:
            yield c

    app.dependency_overrides.pop(current_patient, None)


def _patches(**overrides):
    """Happy-path mocks for every dependency the endpoint touches."""
    mocks = {
        "get_patient_account": AsyncMock(return_value=_ACCOUNT),
        "get_hospital_id_for_clinic": AsyncMock(return_value=1),
        "hospital_bookable": AsyncMock(return_value=True),
        "resolve_booking_fee": AsyncMock(return_value=0),  # fee-free by default = today's flow
        "get_doctor": AsyncMock(return_value={"id": 5, "name": "Rahim"}),
        "get_available_slots": AsyncMock(return_value=list(_SLOTS)),
        "get_or_create_patient": AsyncMock(return_value={"id": 99}),
        "db_book_appointment": AsyncMock(return_value={"id": "apt-1", "serial_number": 3}),
        "send_booking_confirmation": AsyncMock(),
        "send_doctor_notification": AsyncMock(),
        "build_visit_record": AsyncMock(return_value={"summary": "ভিজিট"}),
        "write_patient_memory": AsyncMock(),
    }
    mocks.update(overrides)
    ctxs = [
        patch(f"api.routes.patient_portal.{name}", new=mock)
        for name, mock in mocks.items()
    ]
    return mocks, ctxs


def _post(client, ctxs, body=_BODY):
    from contextlib import ExitStack

    with ExitStack() as stack:
        for c in ctxs:
            stack.enter_context(c)
        return client.post("/patient/appointments", json=body)


def test_direct_booking_success(client):
    mocks, ctxs = _patches()
    r = _post(client, ctxs)
    assert r.status_code == 201
    assert r.json() == {
        "id": "apt-1", "serial_number": 3, "slot_label": _BODY["slot_label"],
        "status": "confirmed", "payment": None,
    }

    # MRN record from the ACCOUNT's identity (not the form).
    goc = mocks["get_or_create_patient"].await_args.kwargs
    assert goc["name"] == "Kodu" and goc["phone"] == "01711000000"
    assert goc["account_id"] == 7 and goc["age"] == 30

    # Appointment row from the FORM, audited as a patient action, no agent thread.
    book = mocks["db_book_appointment"].await_args.kwargs
    assert book["patient_id"] == 99 and book["doctor_id"] == 5
    assert book["patient_mobile"] == "01711000000"  # dash stripped
    assert book["session_id"] is None
    assert book["actor_role"] == "patient"

    # Cross-session memory recorded with the graph's store.
    mem = mocks["write_patient_memory"].await_args.kwargs
    assert mem["account_id"] == 7 and mem["appointment_id"] == "apt-1"


def test_direct_booking_stale_slot_conflicts(client):
    _, ctxs = _patches()
    r = _post(client, ctxs, {**_BODY, "slot_datetime": "2026-07-14T10:00:00"})
    assert r.status_code == 409
    assert r.json()["detail"] == "slot_taken"


def test_direct_booking_race_loss_conflicts(client):
    _, ctxs = _patches(db_book_appointment=AsyncMock(return_value=None))
    r = _post(client, ctxs)
    assert r.status_code == 409


def test_direct_booking_bad_mobile_rejected(client):
    _, ctxs = _patches()
    # 10 chars (passes the schema length) but only 9 digits → digit guard 422.
    r = _post(client, ctxs, {**_BODY, "patient_mobile": "01711-0000"})
    assert r.status_code == 422


def test_direct_booking_bangla_digits_normalized(client):
    mocks, ctxs = _patches()
    r = _post(client, ctxs, {**_BODY, "patient_mobile": "০১৭১১০০০০০০"})
    assert r.status_code == 201
    assert mocks["db_book_appointment"].await_args.kwargs["patient_mobile"] == "01711000000"


def test_direct_booking_unknown_clinic_404(client):
    _, ctxs = _patches(get_hospital_id_for_clinic=AsyncMock(return_value=None))
    r = _post(client, ctxs)
    assert r.status_code == 404


def test_direct_booking_doctor_not_in_clinic_404(client):
    _, ctxs = _patches(get_doctor=AsyncMock(return_value=None))
    r = _post(client, ctxs)
    assert r.status_code == 404


def test_direct_booking_sms_failure_still_books(client):
    _, ctxs = _patches(send_booking_confirmation=AsyncMock(side_effect=RuntimeError("smtp down")))
    r = _post(client, ctxs)
    assert r.status_code == 201


def test_direct_booking_memory_failure_still_books(client):
    _, ctxs = _patches(write_patient_memory=AsyncMock(side_effect=RuntimeError("store down")))
    r = _post(client, ctxs)
    assert r.status_code == 201


def test_direct_booking_billing_suspended_hospital_404s(client):
    _, ctxs = _patches(hospital_bookable=AsyncMock(return_value=False))
    r = _post(client, ctxs)
    assert r.status_code == 404


def test_direct_booking_with_fee_holds_and_returns_payment_prompt(client):
    """A department with a booking fee holds the slot (pending_payment) and
    returns a pay prompt instead of confirming immediately — no SMS/memory
    yet, that happens only once the fee is actually paid."""
    mocks, ctxs = _patches(
        resolve_booking_fee=AsyncMock(return_value=30),
        db_book_appointment=AsyncMock(return_value={"id": "apt-2", "serial_number": 1}),
    )
    payment_row = {
        "id": "pay-1", "kind": "booking_fee", "appointment_id": "apt-2",
        "account_id": 7, "hospital_id": 1, "amount": 30, "currency": "BDT",
        "provider": "manual", "provider_ref": "ref-1", "status": "initiated",
    }
    provider = MagicMock()
    provider.initiate = AsyncMock(return_value={"pay_url": "http://pay.example/x"})
    with (
        patch("api.routes.patient_portal.create_payment", new=AsyncMock(return_value=payment_row)),
        patch("api.routes.patient_portal.get_provider", return_value=provider),
    ):
        r = _post(client, ctxs)
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "pending_payment"
    assert body["payment"]["payment_id"] == "pay-1"
    assert body["payment"]["amount"] == 30
    assert body["payment"]["pay_url"] == "http://pay.example/x"
    mocks["send_booking_confirmation"].assert_not_awaited()
    mocks["write_patient_memory"].assert_not_awaited()


def test_direct_booking_with_fee_autopay_confirms_immediately(client):
    """The manual provider's autopay mode skips the pay step entirely —
    confirms straight away and does the normal SMS/memory follow-up."""
    mocks, ctxs = _patches(
        resolve_booking_fee=AsyncMock(return_value=30),
        db_book_appointment=AsyncMock(return_value={"id": "apt-3", "serial_number": 2}),
    )
    payment_row = {
        "id": "pay-2", "kind": "booking_fee", "appointment_id": "apt-3",
        "account_id": 7, "hospital_id": 1, "amount": 30, "currency": "BDT",
        "provider": "manual", "provider_ref": "ref-2", "status": "initiated",
    }
    provider = MagicMock()
    provider.initiate = AsyncMock(return_value={"auto_paid": True})
    with (
        patch("api.routes.patient_portal.create_payment", new=AsyncMock(return_value=payment_row)),
        patch("api.routes.patient_portal.get_provider", return_value=provider),
        patch("api.routes.patient_portal.confirm_paid_booking", new=AsyncMock()) as confirm,
    ):
        r = _post(client, ctxs)
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "confirmed"
    assert body["payment"] is None
    confirm.assert_awaited_once()
    mocks["send_booking_confirmation"].assert_awaited_once()
    mocks["write_patient_memory"].assert_awaited_once()


# ---------------------------------------------------------------------------
# POST /patient/appointments/{id}/pay — re-initiate a still-pending payment
# ---------------------------------------------------------------------------

_PENDING_APPT = {
    "id": "apt-9", "status": "pending_payment", "clinic_id": 2, "hospital_id": 1,
    "patient_name": "Kodu", "patient_mobile": "01711000000",
    "payment_expires_at": "2026-07-13T09:15:00+06:00",
}


def test_pay_again_reinitiates_and_returns_prompt(client):
    provider = MagicMock()
    provider.initiate = AsyncMock(return_value={"pay_url": "http://pay.example/y"})
    with (
        patch("api.routes.patient_portal.list_appointments_for_account",
              new=AsyncMock(return_value=[_PENDING_APPT])),
        patch("api.routes.patient_portal.resolve_booking_fee", new=AsyncMock(return_value=30)),
        patch("api.routes.patient_portal.create_payment",
              new=AsyncMock(return_value={"id": "pay-9"})),
        patch("api.routes.patient_portal.get_provider", return_value=provider),
    ):
        r = client.post("/patient/appointments/apt-9/pay")
    assert r.status_code == 200
    body = r.json()
    assert body["payment_id"] == "pay-9"
    assert body["pay_url"] == "http://pay.example/y"


def test_pay_again_404_when_not_pending(client):
    with patch("api.routes.patient_portal.list_appointments_for_account",
               new=AsyncMock(return_value=[{**_PENDING_APPT, "status": "confirmed"}])):
        r = client.post("/patient/appointments/apt-9/pay")
    assert r.status_code == 404


def test_pay_again_404_when_not_owned(client):
    with patch("api.routes.patient_portal.list_appointments_for_account",
               new=AsyncMock(return_value=[])):
        r = client.post("/patient/appointments/apt-9/pay")
    assert r.status_code == 404


def test_pay_again_autopay_confirms_and_notifies(client):
    provider = MagicMock()
    provider.initiate = AsyncMock(return_value={"auto_paid": True})
    confirmed_appt = {
        "clinic_id": 2, "doctor_id": 5, "patient_name": "Kodu", "patient_mobile": "01711000000",
        "serial_number": 3, "patient_age": 30, "slot_label": "সোমবার সকাল ৯টা",
    }
    with (
        patch("api.routes.patient_portal.list_appointments_for_account",
              new=AsyncMock(return_value=[_PENDING_APPT])),
        patch("api.routes.patient_portal.resolve_booking_fee", new=AsyncMock(return_value=30)),
        patch("api.routes.patient_portal.create_payment",
              new=AsyncMock(return_value={"id": "pay-9"})),
        patch("api.routes.patient_portal.get_provider", return_value=provider),
        patch("api.routes.patient_portal.confirm_paid_booking",
              new=AsyncMock(return_value={"status": "ok", "appointment": confirmed_appt})),
        patch("api.routes.patient_portal._notify_and_remember", new=AsyncMock()) as notify,
    ):
        r = client.post("/patient/appointments/apt-9/pay")
    assert r.status_code == 200
    assert r.json()["pay_url"] is None
    notify.assert_awaited_once()
