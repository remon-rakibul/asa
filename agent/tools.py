"""LangGraph @tool definitions for appointment booking.

The LLM calls these during the ReAct loop. Tool results flow through
ToolMessages in the conversation history, so the model can see what it already
knows without separate phase tracking.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.config import get_stream_writer
from langgraph.prebuilt import InjectedState
from langgraph.types import Command, interrupt

from config import settings
from tools.database import (
    book_appointment as _book_appointment,
    cancel_appointment_for_account as _cancel_appointment_for_account,
    confirm_paid_booking as _confirm_paid_booking,
    create_escalation as _create_escalation,
    create_payment as _create_payment,
    get_agent_bookings_used as _get_agent_bookings_used,
    get_available_slots as _get_available_slots,
    get_doctor as _get_doctor,
    get_doctors_for_clinic as _get_doctors_for_clinic,
    get_hospital_id_for_clinic as _get_hospital_id_for_clinic,
    get_or_create_patient as _get_or_create_patient,
    get_patient_account as _get_patient_account,
    increment_agent_bookings as _increment_agent_bookings,
    list_appointments_for_account as _list_appointments_for_account,
    patient_tier as _patient_tier,
    reschedule_appointment_for_account as _reschedule_appointment_for_account,
    resolve_booking_fee as _resolve_booking_fee,
    search_doctors_platform as _search_doctors_platform,
)
from tools.payments import get_provider as _get_payment_provider, new_provider_ref
from tools.rag import search_docs as _search_docs
from tools.sms import send_booking_confirmation, send_doctor_notification
from utils.text import normalize_bangla_digits

from .state import AppointmentState

log = logging.getLogger(__name__)

_bg_tasks: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


async def _attach_next_slot(row: dict) -> None:
    """Annotate one search-result row with its next open slot (or None)."""
    try:
        slots = await _get_available_slots(
            row["clinic_id"], days_ahead=7, limit=1, doctor_id=row["id"]
        )
    except Exception:
        slots = []
    row["next_slot"] = slots[0] if slots else None


def _doctor_line(i: int, d: dict) -> str:
    """One compact data line per doctor — the model paraphrases, never quotes."""
    line = f"{i + 1}. {d['name']}"
    if d.get("degrees"):
        line += f", {d['degrees']}"
    if d.get("specialty"):
        line += f" ({d['specialty']})"
    line += f" — {d['hospital_name']}, {d['department_name']}"
    parts = []
    fees = _fee_str(d)
    if fees:
        parts.append(fees)
    if d.get("review_count"):
        parts.append(f"রেটিং {d['avg_rating']}★ ({d['review_count']})")
    if d.get("next_slot"):
        parts.append(f"পরবর্তী ফাঁকা: {d['next_slot']['label']}")
    if parts:
        line += " | " + " | ".join(parts)
    return line


def _doctor_entries(rows: list[dict]) -> list[dict]:
    """State entries for available_doctors — choose_doctor resolves numbers
    against these, and clinic_id/hospital_id land the thread on the clinic."""
    return [
        {
            "id": d["id"], "name": d["name"],
            "clinic_id": d["clinic_id"], "hospital_id": d["hospital_id"],
            "hospital_name": d["hospital_name"],
        }
        for d in rows
    ]


async def _alternative_doctors(doctor_id: int) -> list[dict]:
    """Same-specialty doctors with an upcoming slot, excluding the given one.

    Used when the patient's chosen doctor has no open slots (platform mode) —
    the patient shouldn't dead-end when another cardiologist is free tomorrow.
    """
    doc = await _get_doctor(doctor_id)
    specialty = (doc or {}).get("specialty")
    if not specialty:
        return []
    rows = await _search_doctors_platform(specialty=specialty, limit=6)
    if not rows:
        rows = await _search_doctors_platform(q=specialty, limit=6)
    rows = [r for r in rows if r["id"] != doctor_id]
    await asyncio.gather(*(_attach_next_slot(r) for r in rows))
    return [r for r in rows if r.get("next_slot")][:3]


def _fee_str(d: dict) -> str:
    """Compact fee segment for doctor listing lines ('' when no fee is set).

    Data only — the model paraphrases fees in its own words; nothing here is
    spoken to the patient verbatim.
    """
    new, followup = d.get("fee_new"), d.get("fee_followup")
    if new is not None and followup is not None:
        return f"ফি: নতুন ৳{new}/ফলো-আপ ৳{followup}"
    if new is not None:
        return f"ফি: ৳{new}"
    if followup is not None:
        return f"ফি: ফলো-আপ ৳{followup}"
    return ""


@tool
async def select_department(
    department_number: int,
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[AppointmentState, InjectedState],
) -> Command:
    """Select a hospital department by its number from the menu.

    Call this when the patient states which department they want to visit.
    department_number is the 1-based index from the list shown to the patient.
    """
    departments = state.get("departments") or []
    if not departments or not (1 <= department_number <= len(departments)):
        return Command(
            update={
                "messages": [ToolMessage(
                    content="INVALID_DEPARTMENT: সঠিক বিভাগ নম্বর দিন।",
                    tool_call_id=tool_call_id,
                )]
            }
        )
    chosen = departments[department_number - 1]
    return Command(
        update={
            "messages": [ToolMessage(
                content=f"DEPARTMENT_SELECTED: {chosen['name']} (clinic_id={chosen['id']})",
                tool_call_id=tool_call_id,
            )],
            "clinic_id": chosen["id"],
            "departments": None,
        }
    )


@tool
async def list_doctors(
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[AppointmentState, InjectedState],
) -> Command:
    """List the available doctors in this clinic for the patient to choose from.

    Call this after collecting name/age/mobile when the clinic has multiple doctors.
    The patient picks a doctor, then you call get_available_slots.
    """
    clinic_id = state.get("clinic_id")
    if not clinic_id:
        return Command(
            update={
                "messages": [ToolMessage(
                    content="NO_CLINIC: বিভাগ নির্বাচন করুন আগে।",
                    tool_call_id=tool_call_id,
                )]
            }
        )
    doctors = await _get_doctors_for_clinic(clinic_id)
    if not doctors:
        return Command(
            update={
                "messages": [ToolMessage(
                    content="NO_DOCTORS: এই বিভাগে কোনো ডাক্তার নেই।",
                    tool_call_id=tool_call_id,
                )]
            }
        )
    def _line(i: int, d: dict) -> str:
        line = f"{i + 1}. {d['name']}"
        if d.get("degrees"):
            line += f", {d['degrees']}"
        if d.get("specialty"):
            line += f" ({d['specialty']})"
        desc = (d.get("description") or "").strip()
        if desc:
            # Short snippet so the model can help the patient choose without
            # bloating the tool message (CPU prefill).
            line += f" — {desc[:120]}"
        fees = _fee_str(d)
        if fees:
            line += f" | {fees}"
        return line

    lines = "\n".join(_line(i, d) for i, d in enumerate(doctors))
    return Command(
        update={
            "messages": [ToolMessage(
                content=f"DOCTORS:\n{lines}",
                tool_call_id=tool_call_id,
            )],
            "available_doctors": doctors,
        }
    )


# Bangla specialty stems → English search terms. Doctor rows store specialty
# in English ("Cardiology"), but patients ask in Bangla ("কার্ডিওলজিস্ট",
# "হৃদরোগের ডাক্তার") — a raw ILIKE can never bridge the script gap. This is
# search normalization (data-level), not patient-facing text.
_BN_SPECIALTY_MAP: dict[str, str] = {
    "কার্ডিও": "cardio", "হৃদ": "cardio", "হার্ট": "cardio",
    "চর্ম": "dermat", "ত্বক": "dermat", "স্কিন": "dermat",
    "শিশু": "pediatr", "বাচ্চা": "pediatr",
    "গাইনি": "gynec", "স্ত্রীরোগ": "gynec", "প্রসূতি": "gynec",
    "নিউরো": "neuro", "স্নায়ু": "neuro",
    "অর্থো": "orthop", "হাড়": "orthop",
    "কিডনি": "nephro", "ইউরো": "urolog",
    "মানসিক": "psychiat", "সাইকিয়াট": "psychiat",
    "চোখ": "ophthal", "দাঁত": "dent",
    "ক্যান্সার": "oncol", "ইএনটি": "ent", "নাক": "ent",
    "মেডিসিন": "medicine", "ডায়াবেটিস": "endocrin", "হরমোন": "endocrin",
    "পেট": "gastro", "গ্যাস্ট্রো": "gastro", "লিভার": "hepat",
    "ফুসফুস": "pulmon", "শ্বাস": "pulmon",
}


async def _search_with_fallbacks(query: str) -> list[dict]:
    """Try the raw query, then Bangla→English specialty stems found in it,
    then individual words — first non-empty result wins. Keeps the tool's one
    lean string param while surviving cross-script and full-sentence queries
    ("সবচেয়ে ভালো কার্ডিওলজিস্ট কে?" must find specialty='Cardiology')."""
    q = (query or "").strip()
    candidates: list[str] = [q]
    for bn, en in _BN_SPECIALTY_MAP.items():
        if bn in q:
            candidates.append(en)
    # Individual words (skip punctuation/short filler) — catches doctor names
    # and single-word specialties buried in a sentence.
    for tok in re.split(r"[\s?,।!]+", q):
        if len(tok) >= 3 and tok not in candidates:
            candidates.append(tok)
    seen: set[str] = set()
    for cand in candidates:
        if cand in seen:
            continue
        seen.add(cand)
        rows = await _search_doctors_platform(q=cand, limit=5)
        if rows:
            return rows
    return []


@tool
async def search_doctors(
    query: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[AppointmentState, InjectedState],
) -> Command:
    """Search doctors across ALL hospitals by specialty, doctor name, or the
    patient's need (Bangla or English, e.g. "কার্ডিওলজিস্ট", "চর্মরোগ", "Dr. Rahim").

    Results include hospital, fees (৳), rating, and the next free time — recommend
    ONLY from these results. After the patient picks one, call
    choose_doctor(doctor_number).
    """
    rows = await _search_with_fallbacks(query)
    if not rows:
        return Command(
            update={
                "messages": [ToolMessage(
                    content="NO_DOCTORS_FOUND: অন্য বিশেষত্ব বা নাম দিয়ে খুঁজুন।",
                    tool_call_id=tool_call_id,
                )]
            }
        )

    await asyncio.gather(*(_attach_next_slot(r) for r in rows))

    lines = "\n".join(_doctor_line(i, d) for i, d in enumerate(rows))
    return Command(
        update={
            "messages": [ToolMessage(
                content=f"DOCTORS:\n{lines}",
                tool_call_id=tool_call_id,
            )],
            # Entries carry clinic_id + hospital_id so choose_doctor can land
            # the platform thread on the right clinic for the booking flow.
            "available_doctors": _doctor_entries(rows),
        }
    )


@tool
async def choose_doctor(
    doctor_number: int,
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[AppointmentState, InjectedState],
) -> Command:
    """Record which doctor the patient chose (by number from the DOCTORS list).

    Call this after the patient states their preferred doctor.
    Then proceed to call get_available_slots.
    """
    doctors = state.get("available_doctors") or []
    if not doctors or not (1 <= doctor_number <= len(doctors)):
        return Command(
            update={
                "messages": [ToolMessage(
                    content="INVALID_DOCTOR: সঠিক ডাক্তার নম্বর দিন।",
                    tool_call_id=tool_call_id,
                )]
            }
        )
    chosen = doctors[doctor_number - 1]
    update: dict = {
        "doctor_id": chosen["id"],
        "available_doctors": None,
    }
    content = f"DOCTOR_CHOSEN: {chosen['name']}"
    # Entries from the cross-hospital search carry their clinic/hospital —
    # choosing one lands the platform thread on that clinic so the normal
    # booking flow (get_available_slots, book_appointment) just works.
    # Department-mode entries (list_doctors) lack the key → unchanged.
    if chosen.get("clinic_id"):
        update["clinic_id"] = chosen["clinic_id"]
        update["hospital_id"] = chosen.get("hospital_id")
        if chosen.get("hospital_name"):
            content += f" ({chosen['hospital_name']})"
    update["messages"] = [ToolMessage(content=content, tool_call_id=tool_call_id)]
    return Command(update=update)


@tool
async def get_available_slots(
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[AppointmentState, InjectedState],
) -> Command:
    """Fetch available appointment slots for this clinic.

    Call this once you have collected the patient's name, age, and mobile
    number (and chosen a doctor if applicable). Returns a numbered list of
    times with their ISO datetimes.
    """
    clinic_id = state.get("clinic_id")
    doctor_id = state.get("doctor_id")
    slots = await _get_available_slots(clinic_id, limit=5, doctor_id=doctor_id)
    offered = [{"label": s["label"], "datetime": s["datetime"]} for s in slots]
    if not slots:
        content = "NO_SLOTS_AVAILABLE"
        # Platform mode: don't dead-end — offer same-specialty doctors who DO
        # have an upcoming slot. Data lines only; the model composes the offer.
        if state.get("platform_mode") and doctor_id:
            try:
                alts = await _alternative_doctors(doctor_id)
            except Exception:
                log.warning("alternative doctor lookup failed", exc_info=True)
                alts = []
            if alts:
                lines = "\n".join(_doctor_line(i, d) for i, d in enumerate(alts))
                content += (
                    "\nALTERNATIVE_DOCTORS (same specialty, have free slots):\n"
                    + lines
                )
                return Command(
                    update={
                        "messages": [
                            ToolMessage(content=content, tool_call_id=tool_call_id)
                        ],
                        "slots_shown": False,
                        "offered_slots": offered,
                        "guard_injected": False,
                        # choose_doctor(n) works directly on the alternatives.
                        "available_doctors": _doctor_entries(alts),
                    }
                )
    else:
        lines = "\n".join(
            f"{i + 1}. {s['label']}  [datetime={s['datetime']}]"
            for i, s in enumerate(slots)
        )
        content = f"AVAILABLE_SLOTS:\n{lines}"
        # Surface the slots to the UI immediately as a structured event so it can
        # render a tappable picker. The stream writer only exists inside a graph
        # stream; tests invoke the tool bare, so swallow its absence.
        try:
            get_stream_writer()({"type": "slots", "slots": offered})
        except Exception:
            pass
    return Command(
        update={
            "messages": [ToolMessage(content=content, tool_call_id=tool_call_id)],
            "slots_shown": bool(slots),
            "offered_slots": offered,
            "guard_injected": False,
        }
    )


@tool
async def book_appointment(
    patient_name: str,
    patient_age: int,
    patient_mobile: str,
    slot_datetime: str,
    slot_label: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[AppointmentState, InjectedState],
) -> Command:
    """Book an appointment after the patient has confirmed their chosen slot.

    Args:
        patient_name: Full name as provided by the patient.
        patient_age: Age in years (integer).
        patient_mobile: Mobile number — digits only, 10-11 digits.
        slot_datetime: Exact ISO datetime string copied from the slots list.
        slot_label: Human-readable label from the slots list (e.g. "সোমবার সকাল ৯টা").
    """
    clinic_id = state.get("clinic_id")

    # Guard against booking slots the model never actually fetched. The LLM
    # sometimes fabricates a slot list (and a slot_datetime) without ever calling
    # get_available_slots, which would book a hallucinated time. Force a real
    # fetch first.
    if not state.get("slots_shown"):
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=(
                            "NO_SLOTS_FETCHED: আগে get_available_slots কল করুন এবং "
                            "রোগীকে আসল স্লট দেখান, তারপর সেই তালিকা থেকে slot_datetime "
                            "হুবহু কপি করে বুক করুন।"
                        ),
                        tool_call_id=tool_call_id,
                    )
                ]
            }
        )

    # Guard against the model passing the Bangla label (e.g. "সোমবার সকাল দশটা")
    # as slot_datetime instead of the ISO datetime from the slots list. An
    # unparseable value used to fall through to a None apt_id and be reported to
    # the patient as "slot taken" — wrong and confusing.
    try:
        datetime.fromisoformat(slot_datetime)
    except (ValueError, TypeError):
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=(
                            "INVALID_DATETIME: slot_datetime অবশ্যই AVAILABLE_SLOTS "
                            "তালিকার [datetime=...] থেকে হুবহু কপি করা ISO datetime হতে "
                            "হবে — বাংলা লেবেল নয়। সঠিক datetime দিয়ে আবার কল করুন।"
                        ),
                        tool_call_id=tool_call_id,
                    )
                ]
            }
        )

    digits = re.sub(r"\D", "", normalize_bangla_digits(patient_mobile))
    # Guard against garbled/hallucinated numbers from voice STT — never book a
    # malformed mobile into a medical record. A BD mobile is 10–11 digits.
    # Logged-in patients registered with a phone, so fall back to that instead
    # of dead-ending the booking on a transcription slip.
    if not (10 <= len(digits) <= 11):
        account_id = state.get("patient_account_id")
        if account_id:
            try:
                account = await _get_patient_account(account_id)
                acct_digits = re.sub(
                    r"\D", "", normalize_bangla_digits((account or {}).get("phone") or "")
                )
                if 10 <= len(acct_digits) <= 11:
                    digits = acct_digits
            except Exception:
                log.warning("account phone fallback failed", exc_info=True)
    if not (10 <= len(digits) <= 11):
        # The already-fetched slot list is still valid — a bad phone number
        # must not force a redundant get_available_slots round-trip.
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=(
                            "INVALID_MOBILE: মোবাইল নম্বরটি স্পষ্টভাবে বোঝা যায়নি "
                            "(১০–১১ সংখ্যা প্রয়োজন)। রোগীকে নম্বরটি আবার বলতে বলুন।"
                        ),
                        tool_call_id=tool_call_id,
                    )
                ],
            }
        )
    mobile = digits

    # Platform-mode threads start with no hospital, so /chat/stream couldn't
    # create the hospital's patient (MRN) record up front. Resolve it lazily
    # now that choose_doctor landed us on a clinic — otherwise the booking
    # wouldn't link to the account (my-appointments, review eligibility).
    patient_id = state.get("patient_id")
    account_id = state.get("patient_account_id")
    if patient_id is None and account_id and clinic_id:
        try:
            hospital_id = await _get_hospital_id_for_clinic(clinic_id)
            account = await _get_patient_account(account_id) if hospital_id else None
            if account:
                patient_row = await _get_or_create_patient(
                    hospital_id=hospital_id,
                    name=account["name"],
                    phone=account["phone"],
                    account_id=account_id,
                )
                patient_id = patient_row["id"]
        except Exception:
            # Booking must not fail because linkage did — it just books unlinked,
            # same as an anonymous telephony caller.
            log.warning("lazy patient linkage failed", exc_info=True)

    # Free-tier monthly cap on AGENT (chat/voice) bookings. Telephony/anonymous
    # callers (no account) are never capped. A capped patient can still book
    # directly from the portal UI (which charges the per-booking fee) — this
    # only limits the AI-assisted convenience. The upgrade card is deterministic
    # chrome; the LLM composes the truthful "you've hit your free limit" line.
    if account_id:
        try:
            capped_account = await _get_patient_account(account_id)
            if capped_account and _patient_tier(capped_account) == "free":
                used = await _get_agent_bookings_used(account_id)
                cap = settings.free_agent_bookings_per_month
                if used >= cap:
                    try:
                        get_stream_writer()({
                            "type": "upgrade", "feature": "chat_bookings",
                            "used": used, "cap": cap,
                        })
                    except Exception:
                        pass
                    return Command(update={"messages": [ToolMessage(
                        content=(
                            f"BOOKING_LIMIT_REACHED: free_monthly_cap={cap}. "
                            "রোগীকে জানান এই মাসের ফ্রি এআই বুকিং সীমা শেষ; "
                            "প্রিমিয়ামে আপগ্রেড করলে সীমাহীন, নয়তো পরের মাসে আবার।"
                        ),
                        tool_call_id=tool_call_id,
                    )]})
        except Exception:
            # A usage-check failure must never block a booking — fail open.
            log.warning("agent-booking cap check failed", exc_info=True)

    # Platform fee: telephony/anonymous callers (no account) are always
    # exempt — they pay at the hospital desk, never through the gateway.
    fee = await _resolve_booking_fee(clinic_id, account_id) if clinic_id else 0
    hold = fee > 0

    result = await _book_appointment(
        clinic_id=clinic_id,
        patient_name=patient_name,
        patient_age=patient_age,
        patient_mobile=mobile,
        scheduled_at=slot_datetime,
        duration_mins=30,
        patient_id=patient_id,
        doctor_id=state.get("doctor_id"),
        appointment_type="opd",
        session_id=state.get("session_id"),
        status="pending_payment" if hold else "confirmed",
        payment_ttl_minutes=settings.payment_ttl_minutes if hold else None,
    )

    # Meter the AI-assisted booking against the monthly free-tier cap. Counts
    # holds and confirmations alike (the patient used the agent to book); the
    # counter only gates FREE accounts, but recording it for everyone keeps the
    # /me usage figure honest if they later downgrade.
    if result and account_id:
        try:
            await _increment_agent_bookings(account_id)
        except Exception:
            log.warning("agent-booking usage increment failed", exc_info=True)

    payment_pending = False
    if result and hold:
        apt_id = result["id"]
        serial_number = result.get("serial_number")
        provider_ref = new_provider_ref()
        payment = await _create_payment(
            kind="booking_fee", amount=fee, provider=settings.payment_provider,
            provider_ref=provider_ref, appointment_id=apt_id,
            account_id=account_id, hospital_id=await _get_hospital_id_for_clinic(clinic_id),
        )
        try:
            init = await _get_payment_provider().initiate(
                payment_id=provider_ref, amount=fee, currency="BDT",
                success_url=f"{settings.public_base_url}/payments/redirect/success",
                fail_url=f"{settings.public_base_url}/payments/redirect/fail",
                cancel_url=f"{settings.public_base_url}/payments/redirect/cancel",
                ipn_url=f"{settings.public_base_url}/payments/ipn/{settings.payment_provider}",
                customer_name=patient_name, customer_phone=mobile,
            )
        except Exception:
            log.warning("payment initiate failed for appointment %s", apt_id, exc_info=True)
            init = {}
        if init.get("auto_paid"):
            # Manual provider's autopay — flip to confirmed right away and
            # fall through to the normal "BOOKED:" path below (SMS/notify/
            # memory all happen exactly as an unpaid booking would).
            await _confirm_paid_booking(payment["id"], val_id="", raw={"auto_paid": True})
        else:
            payment_pending = True
            # Deterministic UI chrome — the pay URL never passes through the
            # LLM (nothing to hallucinate). ChatPanel/voice render this as a
            # pay-now card; bare-tool tests have no stream writer, so this is
            # best-effort.
            try:
                expires_at = (
                    datetime.now(timezone.utc)
                    + timedelta(minutes=settings.payment_ttl_minutes)
                ).isoformat()
                get_stream_writer()({
                    "type": "payment", "appointment_id": apt_id,
                    "payment_id": payment["id"], "amount": fee, "currency": "BDT",
                    "pay_url": init.get("pay_url"), "expires_at": expires_at,
                })
            except Exception:
                pass

    if result and not payment_pending:
        apt_id = result["id"]
        serial_number: int | None = result.get("serial_number")
        _spawn(
            send_booking_confirmation(
                mobile, patient_name, slot_label,
                clinic_id=clinic_id,
                serial_number=serial_number,
            )
        )
        _spawn(
            send_doctor_notification(
                patient_name, patient_age, mobile, slot_label, clinic_id=clinic_id
            )
        )
        serial_part = f", serial_number={serial_number}" if serial_number else ""
        content = f"BOOKED: appointment_id={apt_id}{serial_part}"
    elif result and payment_pending:
        serial_part = f", serial_number={serial_number}" if serial_number else ""
        content = (
            f"BOOKED_PENDING_PAYMENT: appointment_id={apt_id}{serial_part}, "
            f"fee=৳{fee}, expires_minutes={settings.payment_ttl_minutes}"
        )
    else:
        # Datetime already validated above, so a None here means a genuine race —
        # the slot was confirmed by someone else. Emit the token the prompt's
        # recovery step (re-fetch slots) keys on.
        apt_id = None
        serial_number = None
        content = "SLOT_TAKEN: ঐ সময়টি অন্য কেউ নিয়ে নিয়েছে।"

    # A held-pending-payment booking is NOT yet a completed booking — keep
    # appointment_id unset so route_after_tools (keyed on the "BOOKED:"
    # prefix, which "BOOKED_PENDING_PAYMENT:" does not match) sends this
    # turn through the normal call_model path, letting the LLM compose the
    # "your slot is held, pay to confirm" reply in its own words instead of
    # the deterministic post_booking farewell.
    confirmed_apt_id = apt_id if (result and not payment_pending) else None
    return Command(
        update={
            "messages": [ToolMessage(content=content, tool_call_id=tool_call_id)],
            "appointment_id": confirmed_apt_id,
            "serial_number": serial_number,
            "patient_id": patient_id,
            "patient_name": patient_name,
            "patient_age": patient_age,
            "patient_mobile": mobile,
            "slot_label": slot_label if confirmed_apt_id else None,
            "slots_shown": False,
            "offered_slots": [] if result else state.get("offered_slots"),
            # A new booking invalidates any previously listed numbering.
            "my_appointments": None,
        }
    )


@tool
async def search_hospital_info(
    query: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[AppointmentState, InjectedState],
) -> Command:
    """Search a hospital's knowledge base to answer patient questions.

    Use this when the patient asks about hospital policies, visiting hours,
    fees, services, directions, or any factual question NOT about booking.
    Do NOT call this for booking-related questions. If the patient hasn't
    chosen a hospital yet, this searches across ALL hospitals on the
    platform and each result names which hospital it's about.
    """
    hospital_id = state.get("hospital_id")
    if not hospital_id:
        clinic_id = state.get("clinic_id")
        if clinic_id:
            hospital_id = await _get_hospital_id_for_clinic(clinic_id)
    # hospital_id stays None here for a platform-mode thread with no
    # hospital chosen yet — search_docs(None, ...) then searches every
    # hospital's documents (see tools/rag.py).

    try:
        chunks = await _search_docs(hospital_id, query, k=4)
    except Exception as exc:
        log.warning("RAG search error for hospital %s: %s", hospital_id, exc)
        chunks = []

    if not chunks:
        return Command(
            update={
                "messages": [ToolMessage(
                    content="NO_INFO: এই প্রশ্নের উত্তর হাসপাতালের তথ্যভাণ্ডারে পাওয়া যায়নি।",
                    tool_call_id=tool_call_id,
                )]
            }
        )

    content = "HOSPITAL_INFO:\n" + "\n---\n".join(chunks)
    return Command(
        update={
            "messages": [ToolMessage(content=content, tool_call_id=tool_call_id)]
        }
    )


@tool
async def request_human_help(
    reason: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[AppointmentState, InjectedState],
) -> Command:
    """Flag this conversation for a human staff member to follow up.

    Call this when the patient explicitly asks for a person, is upset, or you
    have repeatedly failed to help them. reason: one short sentence describing
    what the patient needs.
    """
    try:
        # Route to wherever the patient is currently engaging: a clinic
        # (department) when chosen, else the hospital level (a hospital-wide
        # queue surfaces this — no more guessing a department), else a pure
        # platform-level escalation (both None) visible only in the platform
        # dashboard, e.g. a marketplace-home question with no hospital yet.
        await _create_escalation(
            clinic_id=state.get("clinic_id"),
            hospital_id=state.get("hospital_id"),
            session_id=state.get("session_id") or "",
            channel=state.get("channel") or "text",
            reason=reason or "",
        )
        content = (
            "ESCALATED: একজন স্টাফ সদস্যকে জানানো হয়েছে; তিনি শীঘ্রই এই "
            "কথোপকথনে উত্তর দেবেন।"
        )
    except Exception as exc:
        log.warning("escalation write failed: %s", exc)
        content = "ESCALATION_FAILED: এখন স্টাফকে জানানো যায়নি।"
    return Command(
        update={"messages": [ToolMessage(content=content, tool_call_id=tool_call_id)]}
    )


# --------------------------------------------------------------------------- #
# Manage existing appointments (authenticated portal patients only).
# Bound conditionally — anonymous sessions keep the lean booking-only schema.
# --------------------------------------------------------------------------- #

def _confirmed(decision) -> bool:
    """Interpret an interrupt resume value as yes/no.

    Chat buttons resume with a bool; free text (typed or spoken) is parsed by
    the runner into a bool before resuming, but accept common strings too.
    """
    if isinstance(decision, bool):
        return decision
    if isinstance(decision, str):
        return decision.strip().lower() in (
            "yes", "true", "1", "১", "হ্যাঁ", "হা", "জি", "জ্বি", "নিশ্চিত", "ঠিক আছে",
        )
    return False


def _appointment_lite(row: dict) -> dict:
    """JSON-safe summary of an appointment row for state + interrupt payloads."""
    when = row.get("scheduled_at")
    when_label = when.strftime("%d %b %Y, %I:%M %p") if when else ""
    return {
        "id": row["id"],
        "label": f"{row.get('department_name') or ''} — {when_label}".strip(" —"),
        "hospital_name": row.get("hospital_name"),
        "department_name": row.get("department_name"),
        "doctor_name": row.get("doctor_name"),
        "scheduled_at": when.isoformat() if when else None,
        "serial_number": row.get("serial_number"),
    }


@tool
async def list_my_appointments(
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[AppointmentState, InjectedState],
) -> Command:
    """List the patient's upcoming confirmed appointments.

    Call this when the patient asks to see, cancel, or move (reschedule) their
    appointments. Returns a numbered list; cancel_appointment and
    reschedule_appointment take the number from this list.
    """
    account_id = state.get("patient_account_id")
    if not account_id:
        return Command(
            update={
                "messages": [ToolMessage(
                    content="NOT_LOGGED_IN: এই সুবিধার জন্য রোগীকে পোর্টালে লগ ইন করতে হবে।",
                    tool_call_id=tool_call_id,
                )]
            }
        )
    rows = await _list_appointments_for_account(account_id)
    now = datetime.now(timezone.utc)
    upcoming = [
        r for r in rows
        if r.get("status") == "confirmed"
        and r.get("scheduled_at") is not None
        and r["scheduled_at"] >= now
    ]
    upcoming.sort(key=lambda r: r["scheduled_at"])
    if not upcoming:
        return Command(
            update={
                "messages": [ToolMessage(
                    content="NO_UPCOMING_APPOINTMENTS", tool_call_id=tool_call_id,
                )],
                "my_appointments": [],
            }
        )
    lite = [_appointment_lite(r) for r in upcoming]
    lines = "\n".join(
        f"{i + 1}. {a['label']}"
        + (f" — ডা. {a['doctor_name']}" if a.get("doctor_name") else "")
        + (f" (সিরিয়াল {a['serial_number']})" if a.get("serial_number") else "")
        for i, a in enumerate(lite)
    )
    return Command(
        update={
            "messages": [ToolMessage(
                content=f"MY_APPOINTMENTS:\n{lines}", tool_call_id=tool_call_id,
            )],
            "my_appointments": lite,
        }
    )


@tool
async def cancel_appointment(
    appointment_number: int,
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[AppointmentState, InjectedState],
) -> Command:
    """Cancel one of the patient's upcoming appointments.

    Call list_my_appointments first; appointment_number is the 1-based number
    from the MY_APPOINTMENTS list. The patient is automatically asked to
    confirm before anything is cancelled — do not ask them yourself.
    """
    account_id = state.get("patient_account_id")
    if not account_id:
        return Command(
            update={
                "messages": [ToolMessage(
                    content="NOT_LOGGED_IN: এই সুবিধার জন্য রোগীকে পোর্টালে লগ ইন করতে হবে।",
                    tool_call_id=tool_call_id,
                )]
            }
        )
    appts = state.get("my_appointments") or []
    if not appts or not (1 <= appointment_number <= len(appts)):
        return Command(
            update={
                "messages": [ToolMessage(
                    content=(
                        "INVALID_APPOINTMENT: আগে list_my_appointments কল করুন এবং "
                        "তালিকা থেকে সঠিক নম্বর দিন।"
                    ),
                    tool_call_id=tool_call_id,
                )]
            }
        )
    appt = appts[appointment_number - 1]

    # Pause the graph for a durable patient confirmation (survives reloads via
    # the checkpointer). The UI shows a confirm card / voice asks the question.
    decision = interrupt({
        "kind": "confirm_cancel",
        "question": f"আপনি কি {appt['label']} অ্যাপয়েন্টমেন্টটি বাতিল করতে চান?",
        "appointment": appt,
    })
    if not _confirmed(decision):
        return Command(
            update={
                "messages": [ToolMessage(
                    content="CANCEL_ABORTED: রোগী বাতিল করতে রাজি হননি।",
                    tool_call_id=tool_call_id,
                )]
            }
        )

    ok = await _cancel_appointment_for_account(account_id, appt["id"])
    if not ok:
        return Command(
            update={
                "messages": [ToolMessage(
                    content="CANCEL_FAILED: অ্যাপয়েন্টমেন্টটি পাওয়া যায়নি বা আগেই বাতিল হয়েছে।",
                    tool_call_id=tool_call_id,
                )],
                "my_appointments": None,
            }
        )
    update: dict = {
        "messages": [ToolMessage(
            content=f"CANCELLED: {appt['label']}", tool_call_id=tool_call_id,
        )],
        "my_appointments": None,
    }
    # If the cancelled appointment is the one this thread booked, clear the
    # booked markers so the session doesn't still read as "done".
    if state.get("appointment_id") == appt["id"]:
        update.update(appointment_id=None, slot_label=None, serial_number=None)
    return Command(update=update)


@tool
async def reschedule_appointment(
    appointment_number: int,
    slot_datetime: str,
    slot_label: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[AppointmentState, InjectedState],
) -> Command:
    """Move one of the patient's upcoming appointments to a new slot.

    Steps: call list_my_appointments, then get_available_slots, then this tool.
    appointment_number is the 1-based number from MY_APPOINTMENTS. slot_datetime
    must be the EXACT ISO value copied from the [datetime=...] of the chosen
    slot in AVAILABLE_SLOTS; slot_label is its Bangla label verbatim. The
    patient is automatically asked to confirm — do not ask them yourself.
    """
    account_id = state.get("patient_account_id")
    if not account_id:
        return Command(
            update={
                "messages": [ToolMessage(
                    content="NOT_LOGGED_IN: এই সুবিধার জন্য রোগীকে পোর্টালে লগ ইন করতে হবে।",
                    tool_call_id=tool_call_id,
                )]
            }
        )
    appts = state.get("my_appointments") or []
    if not appts or not (1 <= appointment_number <= len(appts)):
        return Command(
            update={
                "messages": [ToolMessage(
                    content=(
                        "INVALID_APPOINTMENT: আগে list_my_appointments কল করুন এবং "
                        "তালিকা থেকে সঠিক নম্বর দিন।"
                    ),
                    tool_call_id=tool_call_id,
                )]
            }
        )
    if not state.get("slots_shown"):
        return Command(
            update={
                "messages": [ToolMessage(
                    content=(
                        "NO_SLOTS_FETCHED: আগে get_available_slots কল করুন এবং রোগীকে "
                        "আসল স্লট দেখান, তারপর সেই তালিকা থেকে slot_datetime হুবহু কপি করুন।"
                    ),
                    tool_call_id=tool_call_id,
                )]
            }
        )
    try:
        datetime.fromisoformat(slot_datetime)
    except (ValueError, TypeError):
        return Command(
            update={
                "messages": [ToolMessage(
                    content=(
                        "INVALID_DATETIME: slot_datetime অবশ্যই AVAILABLE_SLOTS তালিকার "
                        "[datetime=...] থেকে হুবহু কপি করা ISO datetime হতে হবে।"
                    ),
                    tool_call_id=tool_call_id,
                )]
            }
        )
    appt = appts[appointment_number - 1]

    decision = interrupt({
        "kind": "confirm_reschedule",
        "question": (
            f"আপনি কি {appt['label']} অ্যাপয়েন্টমেন্টটি {slot_label} সময়ে "
            "পরিবর্তন করতে চান?"
        ),
        "appointment": appt,
        "slot_label": slot_label,
    })
    if not _confirmed(decision):
        return Command(
            update={
                "messages": [ToolMessage(
                    content="RESCHEDULE_ABORTED: রোগী পরিবর্তন করতে রাজি হননি।",
                    tool_call_id=tool_call_id,
                )]
            }
        )

    result = await _reschedule_appointment_for_account(account_id, appt["id"], slot_datetime)
    if result["status"] == "slot_taken":
        content = "SLOT_TAKEN: ঐ সময়টি অন্য কেউ নিয়ে নিয়েছে।"
    elif result["status"] == "ok":
        content = f"RESCHEDULED: {slot_label}"
        # Same SMS confirmation the portal's reschedule button sends.
        moved = result.get("appointment") or {}
        if moved.get("patient_mobile"):
            _spawn(
                send_booking_confirmation(
                    moved["patient_mobile"], moved.get("patient_name") or "",
                    slot_label,
                    clinic_id=moved.get("clinic_id"),
                    serial_number=moved.get("serial_number"),
                )
            )
    else:
        content = "RESCHEDULE_FAILED: অ্যাপয়েন্টমেন্টটি পাওয়া যায়নি বা পরিবর্তনযোগ্য নয়।"
    return Command(
        update={
            "messages": [ToolMessage(content=content, tool_call_id=tool_call_id)],
            "my_appointments": None,
            "slots_shown": False,
            "offered_slots": [] if result["status"] == "ok" else state.get("offered_slots"),
        }
    )


BOOKING_TOOLS = [
    select_department,
    list_doctors,
    choose_doctor,
    get_available_slots,
    book_appointment,
    request_human_help,  # safety valve — bound for every session
]

# Full tool list including RAG search (added dynamically when hospital has documents).
TOOLS = BOOKING_TOOLS + [search_hospital_info]

# Manage tools — bound only for authenticated portal patients.
MANAGE_TOOLS = [list_my_appointments, cancel_appointment, reschedule_appointment]

# Cross-hospital marketplace search — bound only for platform-mode threads
# (marketplace home assistant); department/hospital sessions never see it.
SEARCH_TOOLS = [search_doctors]

# Everything ToolNode must be able to execute (binding stays conditional).
ALL_TOOLS = TOOLS + MANAGE_TOOLS + SEARCH_TOOLS
