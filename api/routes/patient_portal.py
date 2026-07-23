"""Patient self-service portal: signup, login, browse, and book.

Platform-wide patient accounts (separate from staff `users`). A patient signs up
once, browses hospitals -> departments -> doctors, and books by chatting with the
LangGraph agent. The first booking at a hospital auto-creates that hospital's
patient (MRN) record and links it to the account.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent.memory import build_visit_record, write_patient_memory
from agent.runner import stream_turn_tokens
from utils.text import normalize_bangla_digits, normalize_bd_mobile
import secrets
from datetime import datetime, timedelta, timezone

from tools.auth import create_patient_token, hash_password, verify_password
from tools.database import (
    account_review_eligible,
    book_appointment as db_book_appointment,
    activate_patient_subscription,
    cancel_appointment_for_account,
    confirm_paid_booking,
    confirm_phone_verification,
    create_password_reset,
    create_patient_account,
    create_payment,
    get_agent_bookings_used,
    delete_conversation,
    delete_conversation_by_session,
    get_active_password_reset,
    get_available_slots,
    get_doctor,
    get_doctor_public,
    get_hospital_id_for_clinic,
    get_or_create_patient,
    get_patient_account,
    get_patient_account_by_email,
    get_patient_account_by_phone,
    get_payment,
    get_phone_verification,
    get_review_for_account,
    get_verified_account_by_phone,
    hospital_bookable,
    increment_phone_verification_attempts,
    list_appointments_for_account,
    list_departments,
    list_doctors as list_clinic_doctors,
    list_hospitals_public,
    list_reviews_for_doctor,
    list_specialties,
    mark_password_reset_used,
    patient_tier,
    reschedule_appointment_for_account,
    resolve_booking_fee,
    search_doctors_platform,
    update_patient_password,
    upsert_phone_verification,
    upsert_review,
)
from tools.payments import get_provider, new_provider_ref
from tools.sms import send_booking_confirmation, send_doctor_notification, send_sms

from config import settings

from ..deps import current_patient
from ..schemas import (
    DepartmentOut,
    DirectBookingIn,
    DirectBookingOut,
    DoctorDetailOut,
    DoctorOut,
    DoctorSearchResult,
    HospitalOut,
    MyReviewOut,
    PatientAccountOut,
    PatientAppointmentOut,
    PatientAppointmentsOut,
    PatientChatRequest,
    PatientLogin,
    PatientPaymentOut,
    PatientSignup,
    PatientSubscriptionOut,
    PatientTokenResponse,
    PasswordForgotRequest,
    PasswordResetRequest,
    PaymentPromptOut,
    PhoneVerifyConfirmRequest,
    PhoneVerifyStartRequest,
    RescheduleRequest,
    ReviewIn,
    ReviewOut,
    SlotOut,
    SpecialtyOut,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/patient", tags=["patient-portal"])

# Deprecated per-clinic thread id — only the legacy /chat/history/{clinic_id}
# routes still read these old threads.
def _stable_session_id(account_id: int, clinic_id: int) -> str:
    return f"pt-acc{account_id}-clinic{clinic_id}"


def _platform_session_id(account_id: int) -> str:
    """The ONE LangGraph thread for this account — every chat and voice
    conversation, from any entry point, lands here. Mirrored in
    appointment-ui/lib/api.ts stablePlatformSessionId() and main.py
    _voice_session_id()."""
    return f"pt-acc{account_id}-platform"


# --- Auth ---

@router.post("/signup", response_model=PatientTokenResponse, status_code=201)
async def signup(body: PatientSignup) -> PatientTokenResponse:
    account = await create_patient_account(
        email=body.email,
        password_hash=hash_password(body.password),
        name=body.name.strip(),
        phone=body.phone.strip(),
    )
    if account is None:
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    token = create_patient_token(account_id=account["id"])
    return PatientTokenResponse(access_token=token, account_id=account["id"])


@router.post("/login", response_model=PatientTokenResponse)
async def login(body: PatientLogin) -> PatientTokenResponse:
    account = await get_patient_account_by_email(body.email)
    if not account or not verify_password(body.password, account["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = create_patient_token(account_id=account["id"])
    return PatientTokenResponse(access_token=token, account_id=account["id"])


async def _find_account(identifier: str) -> dict | None:
    """Resolve a patient account by email or registered phone."""
    identifier = identifier.strip()
    if "@" in identifier:
        return await get_patient_account_by_email(identifier.lower())
    return await get_patient_account_by_phone(identifier)


@router.post("/password/forgot")
async def password_forgot(body: PasswordForgotRequest) -> dict:
    """Send a 6-digit SMS reset code to the account's registered phone.

    Always returns 200 (no account enumeration). The code is single-use and
    expires in 10 minutes.
    """
    account = await _find_account(body.identifier)
    if account and account.get("phone"):
        code = f"{secrets.randbelow(1_000_000):06d}"
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
        await create_password_reset(account["id"], hash_password(code), expires_at)
        try:
            await send_sms(
                account["phone"],
                f"আপনার পাসওয়ার্ড রিসেট কোড: {code} (১০ মিনিটের জন্য বৈধ)।",
            )
        except Exception:
            pass
    return {"ok": True}


@router.post("/password/reset", response_model=PatientTokenResponse)
async def password_reset(body: PasswordResetRequest) -> PatientTokenResponse:
    """Verify the SMS code and set a new password; returns a fresh login token."""
    account = await _find_account(body.identifier)
    if account:
        reset = await get_active_password_reset(account["id"])
        if reset and verify_password(body.code, reset["code_hash"]):
            await update_patient_password(account["id"], hash_password(body.new_password))
            await mark_password_reset_used(reset["id"])
            token = create_patient_token(account_id=account["id"])
            return PatientTokenResponse(access_token=token, account_id=account["id"])
    raise HTTPException(status_code=400, detail="Invalid or expired reset code.")


# --- One-time phone verification (unlocks calling the platform number) ---

_OTP_TTL_MINUTES = 10
_OTP_RESEND_COOLDOWN_SECONDS = 60
_OTP_MAX_ATTEMPTS = 5


@router.post("/phone/verify/start")
async def phone_verify_start(
    body: PhoneVerifyStartRequest, patient: dict = Depends(current_patient)
) -> dict:
    """SMS a 6-digit code to the number the patient wants to verify.

    Verification happens ONCE per account; afterwards caller-ID matching lets
    a premium/trial patient call the platform number. Re-sends are cooled down
    (SMS-bombing/cost guard) and a number already verified by another account
    is rejected up front (one number = one account)."""
    phone = normalize_bd_mobile(body.phone)
    if len(phone) != 11 or not phone.startswith("01"):
        raise HTTPException(status_code=400,
                            detail="Enter a valid BD mobile number (01XXXXXXXXX).")

    account = await get_patient_account(patient["account_id"])
    if account and account.get("phone_verified_at"):
        raise HTTPException(status_code=409, detail="Phone already verified.")

    owner = await get_verified_account_by_phone(phone)
    if owner and owner["id"] != patient["account_id"]:
        raise HTTPException(status_code=409,
                            detail="This number is already linked to another account.")

    pending = await get_phone_verification(patient["account_id"])
    if pending and (datetime.now(timezone.utc) - pending["created_at"]).total_seconds() \
            < _OTP_RESEND_COOLDOWN_SECONDS:
        raise HTTPException(status_code=429,
                            detail="Please wait a minute before requesting another code.")

    code = f"{secrets.randbelow(1_000_000):06d}"
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=_OTP_TTL_MINUTES)
    await upsert_phone_verification(patient["account_id"], phone, hash_password(code), expires_at)
    await send_sms(
        phone,
        f"আপনার নম্বর যাচাইয়ের কোড: {code} ({_OTP_TTL_MINUTES} মিনিটের জন্য বৈধ)।",
    )
    return {"ok": True}


@router.post("/phone/verify/confirm")
async def phone_verify_confirm(
    body: PhoneVerifyConfirmRequest, patient: dict = Depends(current_patient)
) -> dict:
    """Check the SMS code; on success the number is permanently verified on
    this account (no per-call re-verification)."""
    pending = await get_phone_verification(patient["account_id"])
    now = datetime.now(timezone.utc)
    if not pending or pending["expires_at"] <= now:
        raise HTTPException(status_code=400, detail="Code expired — request a new one.")
    if pending["attempts"] >= _OTP_MAX_ATTEMPTS:
        raise HTTPException(status_code=400,
                            detail="Too many wrong attempts — request a new code.")
    if not verify_password(body.code, pending["code_hash"]):
        await increment_phone_verification_attempts(patient["account_id"])
        raise HTTPException(status_code=400, detail="Wrong code.")

    outcome = await confirm_phone_verification(patient["account_id"], pending["phone"])
    if outcome == "phone_taken":
        raise HTTPException(status_code=409,
                            detail="This number is already linked to another account.")
    return {"ok": True, "phone": pending["phone"], "phone_verified": True}


@router.get("/me", response_model=PatientAccountOut)
async def me(patient: dict = Depends(current_patient)) -> PatientAccountOut:
    account = await get_patient_account(patient["account_id"])
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    tier = patient_tier(account)
    used = await get_agent_bookings_used(account["id"])
    return PatientAccountOut(
        id=account["id"], email=account["email"], name=account["name"],
        phone=account["phone"], created_at=account["created_at"],
        plan=account.get("plan", "free"), tier=tier,
        trial_ends_at=account.get("trial_ends_at"),
        premium_until=account.get("premium_until"),
        agent_bookings_used=used,
        # Premium/trial patients are uncapped; expose that as -1 so the UI can
        # render "unlimited" without special-casing the tier name.
        agent_bookings_cap=(-1 if tier != "free" else settings.free_agent_bookings_per_month),
        subscription_fee=settings.patient_subscription_fee,
        phone_verified=bool(account.get("phone_verified_at")),
    )


# --- Browse (any logged-in patient) ---

@router.get("/hospitals", response_model=list[HospitalOut])
async def browse_hospitals(_patient: dict = Depends(current_patient)) -> list[dict]:
    return await list_hospitals_public()


@router.get("/hospitals/{hospital_id}/departments", response_model=list[DepartmentOut])
async def browse_departments(
    hospital_id: int, _patient: dict = Depends(current_patient)
) -> list[dict]:
    return await list_departments(hospital_id)


@router.get("/departments/{clinic_id}/doctors", response_model=list[DoctorOut])
async def browse_doctors(
    clinic_id: int, _patient: dict = Depends(current_patient)
) -> list[dict]:
    return await list_clinic_doctors(clinic_id)


# --- Marketplace: cross-hospital doctor search (foodpanda-style browse) ---

async def _attach_next_slots(rows: list[dict], days_ahead: int = 7) -> None:
    """Set row["next_slot"] for each search result — the card's "From 20 min".

    Per-doctor get_available_slots(limit=1) fanned out concurrently. A failure
    for one doctor just leaves that card without a time chip; page size is
    capped so this stays a few dozen cheap local queries at most.
    """

    async def one(row: dict) -> None:
        try:
            slots = await get_available_slots(
                row["clinic_id"], days_ahead=days_ahead, limit=1,
                doctor_id=row["id"],
            )
        except Exception:
            slots = []
        row["next_slot"] = slots[0] if slots else None

    await asyncio.gather(*(one(r) for r in rows))


@router.get("/specialties", response_model=list[SpecialtyOut])
async def browse_specialties(_patient: dict = Depends(current_patient)) -> list[dict]:
    """Specialty tiles for the portal home (like foodpanda's cuisine tiles)."""
    return await list_specialties()


@router.get("/doctors/search", response_model=list[DoctorSearchResult])
async def search_doctors(
    q: str = "",
    specialty: str | None = None,
    hospital_id: int | None = None,
    max_fee: int | None = None,
    sort: str = "rating",
    page: int = 0,
    _patient: dict = Depends(current_patient),
) -> list[dict]:
    """Cross-hospital doctor search with fees, ratings, and next available slot."""
    # For earliest-available sort we fetch extra candidates, compute next
    # slots, then order by soonest; other sorts are ordered in SQL.
    limit = 40 if sort == "available" else 20
    rows = await search_doctors_platform(
        q=q, specialty=specialty, hospital_id=hospital_id, max_fee=max_fee,
        sort=sort, limit=limit, offset=max(page, 0) * 20,
    )
    await _attach_next_slots(rows)
    if sort == "available":
        rows.sort(
            key=lambda r: (r["next_slot"] is None,
                           (r["next_slot"] or {}).get("datetime", ""))
        )
        rows = rows[:20]
    return rows


@router.get("/doctors/{doctor_id}", response_model=DoctorDetailOut)
async def doctor_detail(
    doctor_id: int, _patient: dict = Depends(current_patient)
) -> dict:
    """Doctor profile page: fees, rating, hospital/department, slot preview."""
    row = await get_doctor_public(doctor_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Doctor not found")
    try:
        row["slots"] = await get_available_slots(
            row["clinic_id"], days_ahead=7, limit=5, doctor_id=doctor_id
        )
    except Exception:
        row["slots"] = []
    row["next_slot"] = row["slots"][0] if row["slots"] else None
    return row


@router.get("/doctors/{doctor_id}/reviews", response_model=list[ReviewOut])
async def doctor_reviews(
    doctor_id: int, _patient: dict = Depends(current_patient)
) -> list[dict]:
    return await list_reviews_for_doctor(doctor_id)


@router.get("/doctors/{doctor_id}/review", response_model=MyReviewOut | None)
async def my_review(
    doctor_id: int, patient: dict = Depends(current_patient)
) -> dict | None:
    """The authenticated patient's own review of this doctor (for edit prefill)."""
    return await get_review_for_account(patient["account_id"], doctor_id)


@router.put("/doctors/{doctor_id}/review", response_model=MyReviewOut)
async def submit_review(
    doctor_id: int, body: ReviewIn, patient: dict = Depends(current_patient)
) -> dict:
    """Create or update the patient's review — allowed only after an
    appointment with this doctor that actually happened."""
    account_id = patient["account_id"]
    if await get_doctor(doctor_id) is None:
        raise HTTPException(status_code=404, detail="Doctor not found")
    if not await account_review_eligible(account_id, doctor_id):
        raise HTTPException(
            status_code=403,
            detail="You can review a doctor only after a completed appointment.",
        )
    review = await upsert_review(account_id, doctor_id, body.rating, body.text)
    return {**review, "doctor_id": doctor_id}


@router.get("/appointments", response_model=PatientAppointmentsOut)
async def my_appointments(patient: dict = Depends(current_patient)) -> PatientAppointmentsOut:
    """The patient's appointments. Free-tier accounts see a limited history —
    older settled bookings are trimmed (held/pending ones always survive, they
    sort first) and `truncated` tells the UI to nudge an upgrade."""
    account = await get_patient_account(patient["account_id"])
    rows = await list_appointments_for_account(patient["account_id"])
    total = len(rows)
    truncated = False
    if account and patient_tier(account) == "free" and total > settings.free_history_limit:
        rows = rows[: settings.free_history_limit]
        truncated = True
    return PatientAppointmentsOut(items=rows, truncated=truncated, total=total)


async def _notify_and_remember(
    *, graph, account_id: int, patient_name: str, patient_mobile: str,
    slot_label: str, clinic_id: int, doctor_id: int | None,
    serial_number: int | None, patient_age: int | None, appointment_id: str,
) -> None:
    """Booking-confirmation SMS + doctor notification + cross-session memory.

    Shared by the immediate-confirm path (fee=0, or the manual provider's
    autopay) and the payment IPN handler (confirmed later, once a real
    gateway payment actually lands) — both reach "this booking is now
    confirmed" and need the exact same follow-up.
    """
    try:
        await send_booking_confirmation(
            patient_mobile, patient_name, slot_label,
            clinic_id=clinic_id, serial_number=serial_number,
        )
        await send_doctor_notification(
            patient_name, patient_age or 0, patient_mobile, slot_label, clinic_id=clinic_id,
        )
    except Exception:
        pass  # notifications are best-effort — the booking already exists

    try:
        store = getattr(graph, "store", None)
        visit = await build_visit_record(
            clinic_id=clinic_id, doctor_id=doctor_id, slot_label=slot_label,
            serial_number=serial_number,
        )
        await write_patient_memory(
            store, account_id=account_id, name=patient_name, age=patient_age,
            phone=patient_mobile, appointment_id=appointment_id, visit=visit,
        )
    except Exception:
        log.warning("booking memory write failed", exc_info=True)


@router.post("/appointments", response_model=DirectBookingOut, status_code=201)
async def book_direct_appointment(
    body: DirectBookingIn,
    request: Request,
    patient: dict = Depends(current_patient),
) -> DirectBookingOut:
    """Direct booking from the portal UI — patient tapped a slot, no agent.

    Mirrors the agent tool's safety rails: the slot must be in the CURRENT
    open-slot list (stale UI / arbitrary datetimes / past slots all fail the
    same membership check) and the mobile must normalize to 10–11 digits.
    Not behind the chat rate-limit bucket by design: it's auth-gated, and the
    slot-membership check + unique-slot constraint bound abuse.

    When the department charges a platform booking fee, the slot is HELD
    (status="pending_payment", a short TTL) instead of confirmed immediately;
    the response carries a `payment` prompt the UI turns into a pay step.
    Confirmation (SMS, doctor notification, memory) happens only once the
    fee is actually paid — either immediately (fee=0, or the manual
    provider's autopay) or later via the gateway's IPN
    (api/routes/payments.py).
    """
    account_id = patient["account_id"]
    account = await get_patient_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    hospital_id = await get_hospital_id_for_clinic(body.clinic_id)
    if hospital_id is None or not await hospital_bookable(hospital_id):
        raise HTTPException(status_code=404, detail="Department not found")

    # Unlike /chat/stream (silently drops a stale doctor param), an explicit
    # UI booking against the wrong department is a hard error.
    if body.doctor_id is not None:
        doctor_row = await get_doctor(body.doctor_id, clinic_id=body.clinic_id)
        if doctor_row is None:
            raise HTTPException(status_code=404, detail="Doctor not found in this department")

    digits = re.sub(r"\D", "", normalize_bangla_digits(body.patient_mobile))
    if not (10 <= len(digits) <= 11):
        raise HTTPException(
            status_code=422, detail="Mobile number must be 10-11 digits"
        )

    # Slot re-validation: the doctor page shows a 7-day preview; 14 covers it.
    slots = await get_available_slots(
        body.clinic_id, days_ahead=14, doctor_id=body.doctor_id
    )
    if body.slot_datetime not in {s["datetime"] for s in slots}:
        raise HTTPException(status_code=409, detail="slot_taken")

    # Same identity policy as /chat/stream: the MRN record carries the
    # ACCOUNT's name/phone; the appointment row carries the form's values —
    # booking for a family member works exactly like the agent path.
    patient_row = await get_or_create_patient(
        hospital_id=hospital_id,
        name=account["name"],
        phone=account["phone"],
        age=body.patient_age,
        account_id=account_id,
    )

    fee = await resolve_booking_fee(body.clinic_id, account_id)
    hold = fee > 0
    result = await db_book_appointment(
        clinic_id=body.clinic_id,
        patient_name=body.patient_name,
        patient_age=body.patient_age,
        patient_mobile=digits,
        scheduled_at=body.slot_datetime,
        patient_id=patient_row["id"],
        doctor_id=body.doctor_id,
        appointment_type="opd",
        session_id=None,
        actor_role="patient",
        status="pending_payment" if hold else "confirmed",
        payment_ttl_minutes=settings.payment_ttl_minutes if hold else None,
    )
    if result is None:
        # Passed the membership check but lost the insert race.
        raise HTTPException(status_code=409, detail="slot_taken")

    serial_number = result.get("serial_number")
    graph = request.app.state.graph
    payment_prompt: PaymentPromptOut | None = None
    status = "confirmed"

    if hold:
        provider_ref = new_provider_ref()
        payment = await create_payment(
            kind="booking_fee", amount=fee, provider=settings.payment_provider,
            provider_ref=provider_ref, appointment_id=result["id"],
            account_id=account_id, hospital_id=hospital_id,
        )
        try:
            init = await get_provider().initiate(
                payment_id=provider_ref, amount=fee, currency="BDT",
                success_url=f"{settings.public_base_url}/payments/redirect/success",
                fail_url=f"{settings.public_base_url}/payments/redirect/fail",
                cancel_url=f"{settings.public_base_url}/payments/redirect/cancel",
                ipn_url=f"{settings.public_base_url}/payments/ipn/{settings.payment_provider}",
                customer_name=body.patient_name, customer_phone=digits,
            )
        except Exception:
            log.warning("payment initiate failed for appointment %s", result["id"], exc_info=True)
            init = {}
        if init.get("auto_paid"):
            await confirm_paid_booking(payment["id"], val_id="", raw={"auto_paid": True})
        else:
            status = "pending_payment"
            payment_prompt = PaymentPromptOut(
                payment_id=payment["id"], amount=fee, currency="BDT",
                pay_url=init.get("pay_url"),
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=settings.payment_ttl_minutes),
            )

    if status == "confirmed":
        await _notify_and_remember(
            graph=graph, account_id=account_id, patient_name=body.patient_name,
            patient_mobile=digits, slot_label=body.slot_label, clinic_id=body.clinic_id,
            doctor_id=body.doctor_id, serial_number=serial_number,
            patient_age=body.patient_age, appointment_id=result["id"],
        )

    return DirectBookingOut(
        id=result["id"], serial_number=serial_number, slot_label=body.slot_label,
        status=status, payment=payment_prompt,
    )


@router.get("/payments/{payment_id}", response_model=PatientPaymentOut)
async def get_my_payment(
    payment_id: str, patient: dict = Depends(current_patient)
) -> PatientPaymentOut:
    """Poll a payment's status (the booking sheet's "waiting for payment" step)."""
    payment = await get_payment(payment_id)
    if payment is None or payment["account_id"] != patient["account_id"]:
        raise HTTPException(status_code=404, detail="Payment not found")
    appointment_status = None
    if payment["appointment_id"]:
        rows = await list_appointments_for_account(patient["account_id"])
        appointment_status = next(
            (r["status"] for r in rows if r["id"] == payment["appointment_id"]), None
        )
    return PatientPaymentOut(
        id=payment["id"], status=payment["status"], amount=payment["amount"],
        currency=payment["currency"], appointment_id=payment["appointment_id"],
        appointment_status=appointment_status,
    )


@router.post("/appointments/{appointment_id}/cancel")
async def cancel_my_appointment(
    appointment_id: str, patient: dict = Depends(current_patient)
) -> dict:
    """Cancel one of the authenticated patient's own confirmed appointments."""
    ok = await cancel_appointment_for_account(patient["account_id"], appointment_id)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail="Appointment not found, not yours, or already cancelled.",
        )
    return {"ok": True}


@router.post("/appointments/{appointment_id}/pay", response_model=PaymentPromptOut)
async def pay_for_appointment(
    appointment_id: str, request: Request, patient: dict = Depends(current_patient),
) -> PaymentPromptOut:
    """Re-initiate payment for a still-held appointment — the my-appointments
    "Pay now" button, for when the patient closed the gateway tab without
    paying the first time. Does not extend the slot's hold — it expires at
    its original TTL regardless of how many payment attempts are made."""
    account_id = patient["account_id"]
    rows = await list_appointments_for_account(account_id)
    appt = next((r for r in rows if r["id"] == appointment_id), None)
    if appt is None or appt["status"] != "pending_payment":
        raise HTTPException(status_code=404, detail="No pending payment for this appointment")

    fee = await resolve_booking_fee(appt["clinic_id"], account_id)
    provider_ref = new_provider_ref()
    payment = await create_payment(
        kind="booking_fee", amount=fee, provider=settings.payment_provider,
        provider_ref=provider_ref, appointment_id=appointment_id,
        account_id=account_id, hospital_id=appt["hospital_id"],
    )
    try:
        init = await get_provider().initiate(
            payment_id=provider_ref, amount=fee, currency="BDT",
            success_url=f"{settings.public_base_url}/payments/redirect/success",
            fail_url=f"{settings.public_base_url}/payments/redirect/fail",
            cancel_url=f"{settings.public_base_url}/payments/redirect/cancel",
            ipn_url=f"{settings.public_base_url}/payments/ipn/{settings.payment_provider}",
            customer_name=appt["patient_name"], customer_phone=appt["patient_mobile"],
        )
    except Exception:
        log.warning("pay-again initiate failed for appointment %s", appointment_id, exc_info=True)
        raise HTTPException(status_code=502, detail="Payment gateway is unavailable, try again shortly")

    if init.get("auto_paid"):
        outcome = await confirm_paid_booking(payment["id"], val_id="", raw={"auto_paid": True})
        if outcome["status"] == "ok" and outcome["appointment"]:
            a = outcome["appointment"]
            await _notify_and_remember(
                graph=request.app.state.graph, account_id=account_id,
                patient_name=a["patient_name"], patient_mobile=a["patient_mobile"],
                slot_label=a["slot_label"], clinic_id=a["clinic_id"], doctor_id=a["doctor_id"],
                serial_number=a["serial_number"], patient_age=a["patient_age"],
                appointment_id=appointment_id,
            )
        return PaymentPromptOut(
            payment_id=payment["id"], amount=fee, currency="BDT", pay_url=None, expires_at=None
        )

    return PaymentPromptOut(
        payment_id=payment["id"], amount=fee, currency="BDT",
        pay_url=init.get("pay_url"), expires_at=appt.get("payment_expires_at"),
    )


@router.post("/subscription/checkout", response_model=PatientSubscriptionOut)
async def subscribe(patient: dict = Depends(current_patient)) -> PatientSubscriptionOut:
    """Buy (or renew) a month of premium. Creates a `patient_subscription`
    payment and hands back a pay prompt; premium activates when the payment
    confirms — immediately for the manual provider's autopay, or later via the
    gateway IPN. Periods are PREPAID and stack (GREATEST(now, premium_until) +
    30d) — BD has no card auto-debit, so renewal is a manual re-purchase."""
    account_id = patient["account_id"]
    account = await get_patient_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    fee = settings.patient_subscription_fee
    provider_ref = new_provider_ref()
    payment = await create_payment(
        kind="patient_subscription", amount=fee, provider=settings.payment_provider,
        provider_ref=provider_ref, appointment_id=None,
        account_id=account_id, hospital_id=None,
    )
    try:
        init = await get_provider().initiate(
            payment_id=provider_ref, amount=fee, currency="BDT",
            success_url=f"{settings.public_base_url}/payments/redirect/success",
            fail_url=f"{settings.public_base_url}/payments/redirect/fail",
            cancel_url=f"{settings.public_base_url}/payments/redirect/cancel",
            ipn_url=f"{settings.public_base_url}/payments/ipn/{settings.payment_provider}",
            customer_name=account["name"], customer_phone=account["phone"],
        )
    except Exception:
        log.warning("subscription initiate failed for account %s", account_id, exc_info=True)
        raise HTTPException(status_code=502, detail="Payment gateway is unavailable, try again shortly")

    if init.get("auto_paid"):
        outcome = await confirm_paid_booking(payment["id"], val_id="", raw={"auto_paid": True})
        if outcome["status"] in ("ok", "already_paid"):
            fresh = await get_patient_account(account_id)
            return PatientSubscriptionOut(
                tier=patient_tier(fresh) if fresh else "premium",
                premium_until=fresh.get("premium_until") if fresh else None,
                payment=PaymentPromptOut(
                    payment_id=payment["id"], amount=fee, currency="BDT",
                    pay_url=None, expires_at=None,
                ),
            )

    return PatientSubscriptionOut(
        tier=patient_tier(account), premium_until=account.get("premium_until"),
        payment=PaymentPromptOut(
            payment_id=payment["id"], amount=fee, currency="BDT",
            pay_url=init.get("pay_url"), expires_at=None,
        ),
    )


@router.get("/departments/{clinic_id}/availability", response_model=list[SlotOut])
async def department_availability(
    clinic_id: int, _patient: dict = Depends(current_patient)
) -> list[dict]:
    """Open slots for a department, used by the patient reschedule picker."""
    return await get_available_slots(clinic_id, days_ahead=14)


@router.post("/appointments/{appointment_id}/reschedule")
async def reschedule_my_appointment(
    appointment_id: str, body: RescheduleRequest,
    patient: dict = Depends(current_patient),
) -> dict:
    """Move one of the patient's own confirmed appointments to a new slot."""
    result = await reschedule_appointment_for_account(
        patient["account_id"], appointment_id, body.slot_datetime
    )
    if result["status"] == "slot_taken":
        raise HTTPException(status_code=409, detail="That slot is already booked")
    if result["status"] != "ok":
        raise HTTPException(status_code=404, detail="Appointment not found or not reschedulable")
    appt = result["appointment"]
    try:
        await send_booking_confirmation(
            appt["patient_mobile"], appt["patient_name"],
            appt["scheduled_at"].strftime("%d %b %Y, %I:%M %p"),
            clinic_id=appt.get("clinic_id"), serial_number=appt.get("serial_number"),
        )
    except Exception:
        pass
    return {"ok": True}


# --- Booking chat (patient-scoped, streaming) ---

class PrewarmRequest(BaseModel):
    clinic_id: int | None = None   # None = platform-wide assistant
    doctor_id: int | None = None


@router.post("/chat/prewarm")
async def patient_chat_prewarm(
    body: PrewarmRequest,
    patient: dict = Depends(current_patient),
) -> dict:
    """Heat the LLM's prompt cache for this patient+department before the chat opens.

    Called fire-and-forget by the portal wizard when the patient reaches the
    doctor step — by the time they land in the chat, the greeting turn's
    prompt prefix is already prefilled and the first token arrives in seconds
    instead of minutes (CPU inference). Read-only: unlike /chat/stream it does
    NOT create the hospital's patient record.
    """
    account_id = patient["account_id"]
    account = await get_patient_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    from agent.nodes import prewarm_turn

    # Every patient thread is platform-mode (one unified thread per account).
    state = {
        "platform_mode": True,
        "patient_account_id": account_id,
        "patient_name": account["name"] or None,
        "patient_mobile": account["phone"] or None,
    }

    if body.clinic_id is not None:
        hospital_id = await get_hospital_id_for_clinic(body.clinic_id)
        if hospital_id is None:
            raise HTTPException(status_code=404, detail="Department not found")

        # Seed the pre-selected doctor exactly like /chat/stream does: the
        # {doctor_context} prompt section differs between PRE-SELECTED and
        # multi-doctor mode, and it sits in the prompt's static head — without
        # the same doctor_id the warmed cache prefix won't match the real turn.
        doctor_id = None
        if body.doctor_id is not None:
            doctor_row = await get_doctor(body.doctor_id, clinic_id=body.clinic_id)
            doctor_id = doctor_row["id"] if doctor_row else None

        state.update(
            clinic_id=body.clinic_id, hospital_id=hospital_id, doctor_id=doctor_id
        )

    asyncio.create_task(prewarm_turn(state))
    return {"ok": True}


@router.post("/chat/stream")
async def patient_chat_stream(
    body: PatientChatRequest,
    request: Request,
    patient: dict = Depends(current_patient),
) -> StreamingResponse:
    """Stream the booking agent for an authenticated patient.

    ONE unified LangGraph thread per account (chat + voice, every entry
    point) — the thread id is derived server-side from the JWT, never taken
    from the client. A clinic/doctor in the request is per-turn context
    (deep link from a doctor page), not a different conversation.
    """
    account_id = patient["account_id"]
    account = await get_patient_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    session_id = _platform_session_id(account_id)

    if body.clinic_id is None:
        # No department context this turn — book_appointment links the
        # hospital's patient (MRN) record lazily once the agent lands on a
        # clinic.
        hospital_id = None
        patient_row = None
        doctor_id = None
    else:
        hospital_id = await get_hospital_id_for_clinic(body.clinic_id)
        if hospital_id is None:
            raise HTTPException(status_code=404, detail="Department not found")

        # Resolve (or create) this hospital's patient record for the account.
        patient_row = await get_or_create_patient(
            hospital_id=hospital_id,
            name=account["name"],
            phone=account["phone"],
            account_id=account_id,
        )

        # Pre-selected doctor from the portal wizard: trust it only if it
        # actually belongs to this clinic — a stale/mismatched id is silently
        # dropped rather than erroring, since a bad URL param shouldn't block
        # the whole turn.
        doctor_id = None
        if body.doctor_id is not None:
            doctor_row = await get_doctor(body.doctor_id, clinic_id=body.clinic_id)
            doctor_id = doctor_row["id"] if doctor_row else None

    graph = request.app.state.graph

    async def event_generator():
        async for event in stream_turn_tokens(
            graph,
            session_id,
            body.message,
            clinic_id=body.clinic_id,
            hospital_id=hospital_id,
            doctor_id=doctor_id,
            channel="web",
            patient_id=patient_row["id"] if patient_row else None,
            patient_account_id=account_id,
            patient_name=account["name"] or None,
            patient_mobile=account["phone"] or None,
            resume=body.resume,
            suggest=True,
            # Every patient thread is platform-mode: one stable prompt head +
            # tool binding for the account's single unified thread, regardless
            # of which page (home, doctor deep-link) this turn came from.
            platform=True,
        ):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _history_for_thread(graph, session_id: str) -> list[dict]:
    """Saved conversation for one LangGraph thread, shaped for the portal UI.

    Only HumanMessage and AIMessage are returned — tool messages are internal.
    Re-attaches the last slot picker and any pending confirm interrupt.
    """
    try:
        config = {"configurable": {"thread_id": session_id}}
        state = await graph.aget_state(config)
        values = state.values or {}
        messages = values.get("messages", [])
    except Exception:
        return []

    result = []
    for msg in messages:
        role = getattr(msg, "type", None)
        if role == "human":
            # Skip empty greeting-trigger messages (send("") starts the agent's
            # opening turn) — they'd render as blank user bubbles after reload.
            if msg.content:
                result.append({"role": "user", "text": msg.content})
        elif role == "ai":
            content = msg.content or ""
            if content:  # skip empty AI messages (pure tool-call turns)
                result.append({"role": "assistant", "text": content})

    # Re-attach the last shown slot picker (if any, and not yet booked) to the
    # final assistant turn so the UI can render the tappable grid after a reload.
    offered = values.get("offered_slots")
    if offered and not values.get("appointment_id"):
        for item in reversed(result):
            if item["role"] == "assistant":
                item["slots"] = offered
                break

    # Pending confirm question (cancel/reschedule interrupt): re-surface it so
    # the UI re-renders the confirm card after a reload instead of stranding
    # the paused thread.
    for intr in getattr(state, "interrupts", None) or ():
        value = getattr(intr, "value", None)
        if isinstance(value, dict) and value.get("question"):
            result.append(
                {"role": "assistant", "text": value["question"], "confirm": value}
            )
            break
    return result


async def _clear_thread(graph, session_id: str, clinic_id: int | None) -> None:
    """Wipe one conversation: LangGraph thread state + conversation_log rows."""
    checkpointer = getattr(graph, "checkpointer", None)
    if checkpointer is not None:
        try:
            await checkpointer.adelete_thread(session_id)
        except Exception:
            # Best-effort: a missing/empty thread is fine — the goal is a clean slate.
            pass
    try:
        if clinic_id is not None:
            await delete_conversation(clinic_id, session_id)
        else:
            # A unified platform thread's turns are scattered across
            # clinic_id values (including NULL) as the patient moves between
            # hospitals — clinic-scoped delete would leave most of it behind.
            await delete_conversation_by_session(session_id)
    except Exception:
        pass


# Literal /platform routes MUST be declared before the int-typed
# /chat/history/{clinic_id} routes or FastAPI 422s on the path.

@router.get("/chat/history/platform")
async def get_platform_chat_history(
    request: Request, patient: dict = Depends(current_patient)
) -> list[dict]:
    """Saved platform-wide assistant conversation for this patient."""
    session_id = _platform_session_id(patient["account_id"])
    return await _history_for_thread(request.app.state.graph, session_id)


@router.delete("/chat/history/platform")
async def clear_platform_chat_history(
    request: Request, patient: dict = Depends(current_patient)
) -> dict:
    session_id = _platform_session_id(patient["account_id"])
    await _clear_thread(request.app.state.graph, session_id, None)
    return {"ok": True}


@router.get("/chat/history/{clinic_id}")
async def get_chat_history(
    clinic_id: int,
    request: Request,
    patient: dict = Depends(current_patient),
) -> list[dict]:
    """Deprecated: the portal now uses one unified thread per account (see
    /chat/history/platform). Kept for old clients that still hold per-clinic
    thread ids."""
    session_id = _stable_session_id(patient["account_id"], clinic_id)
    return await _history_for_thread(request.app.state.graph, session_id)


@router.delete("/chat/history/{clinic_id}")
async def clear_chat_history(
    clinic_id: int,
    request: Request,
    patient: dict = Depends(current_patient),
) -> dict:
    """Deprecated: the portal "New conversation" button now clears the unified
    platform thread. Kept for old clients with per-clinic thread ids."""
    session_id = _stable_session_id(patient["account_id"], clinic_id)
    await _clear_thread(request.app.state.graph, session_id, clinic_id)
    return {"ok": True}
