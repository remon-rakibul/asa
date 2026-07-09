"""LangGraph @tool definitions for appointment booking.

The LLM calls these during the ReAct loop. Tool results flow through
ToolMessages in the conversation history, so the model can see what it already
knows without separate phase tracking.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.config import get_stream_writer
from langgraph.prebuilt import InjectedState
from langgraph.types import Command, interrupt

from tools.database import (
    book_appointment as _book_appointment,
    cancel_appointment_for_account as _cancel_appointment_for_account,
    create_escalation as _create_escalation,
    get_available_slots as _get_available_slots,
    get_doctors_for_clinic as _get_doctors_for_clinic,
    get_hospital_id_for_clinic as _get_hospital_id_for_clinic,
    list_appointments_for_account as _list_appointments_for_account,
    list_departments as _list_departments,
    reschedule_appointment_for_account as _reschedule_appointment_for_account,
)
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
    return Command(
        update={
            "messages": [ToolMessage(
                content=f"DOCTOR_CHOSEN: {chosen['name']}",
                tool_call_id=tool_call_id,
            )],
            "doctor_id": chosen["id"],
            "available_doctors": None,
        }
    )


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
    if not (10 <= len(digits) <= 11):
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
                "slots_shown": False,
            }
        )
    mobile = digits

    result = await _book_appointment(
        clinic_id=clinic_id,
        patient_name=patient_name,
        patient_age=patient_age,
        patient_mobile=mobile,
        scheduled_at=slot_datetime,
        duration_mins=30,
        patient_id=state.get("patient_id"),
        doctor_id=state.get("doctor_id"),
        appointment_type="opd",
        session_id=state.get("session_id"),
    )

    if result:
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
    else:
        # Datetime already validated above, so a None here means a genuine race —
        # the slot was confirmed by someone else. Emit the token the prompt's
        # recovery step (re-fetch slots) keys on.
        apt_id = None
        serial_number = None
        content = "SLOT_TAKEN: ঐ সময়টি অন্য কেউ নিয়ে নিয়েছে।"

    return Command(
        update={
            "messages": [ToolMessage(content=content, tool_call_id=tool_call_id)],
            "appointment_id": apt_id,
            "serial_number": serial_number,
            "patient_name": patient_name,
            "patient_age": patient_age,
            "patient_mobile": mobile,
            "slot_label": slot_label if apt_id else None,
            "slots_shown": False,
            "offered_slots": [] if apt_id else state.get("offered_slots"),
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
    """Search this hospital's knowledge base to answer patient questions.

    Use this when the patient asks about hospital policies, visiting hours,
    fees, services, directions, or any factual question NOT about booking.
    Do NOT call this for booking-related questions.
    """
    hospital_id = state.get("hospital_id")
    if not hospital_id:
        clinic_id = state.get("clinic_id")
        if clinic_id:
            hospital_id = await _get_hospital_id_for_clinic(clinic_id)

    if not hospital_id:
        return Command(
            update={
                "messages": [ToolMessage(
                    content="NO_INFO: হাসপাতালের তথ্যভাণ্ডার পাওয়া যায়নি।",
                    tool_call_id=tool_call_id,
                )]
            }
        )

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
        # A hospital-level IVR call/chat can ask for a human before a
        # department is chosen (clinic_id still None) — escalations are only
        # ever surfaced in a per-clinic admin queue (no hospital-level view),
        # so attach it to the hospital's first department rather than writing
        # a clinic_id=NULL row, which no admin queue query would ever match
        # and would sit unresolved forever.
        clinic_id = state.get("clinic_id")
        if clinic_id is None and state.get("hospital_id"):
            departments = await _list_departments(state["hospital_id"])
            if departments:
                clinic_id = departments[0]["id"]
        await _create_escalation(
            clinic_id=clinic_id,
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

# Everything ToolNode must be able to execute (binding stays conditional).
ALL_TOOLS = TOOLS + MANAGE_TOOLS
