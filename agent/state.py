"""LangGraph conversation state for the appointment-setter agent."""

from typing import Annotated, Optional, TypedDict

from langgraph.graph import add_messages


class AppointmentState(TypedDict, total=False):
    # Full conversation history. add_messages appends per turn.
    messages: Annotated[list, add_messages]

    # Tenant scope — set on the first turn, persisted by the checkpointer.
    clinic_id: Optional[int]
    hospital_id: Optional[int]
    channel: Optional[str]
    session_id: Optional[str]
    channel_identifier: Optional[str]

    # Collected patient details (written by the book_appointment tool via Command).
    patient_name: Optional[str]
    patient_age: Optional[int]
    patient_mobile: Optional[str]

    # Authenticated patient-portal identity. When set, the booking is linked to a
    # known patient and the agent can skip re-collecting identity.
    patient_id: Optional[int]
    patient_account_id: Optional[int]

    # Set once the appointment row is written.
    appointment_id: Optional[str]
    slot_label: Optional[str]   # stored by book_appointment for deterministic farewell
    serial_number: Optional[int]  # daily sequence number for this clinic (সিরিয়াল নম্বর)

    # Set to True by get_available_slots so the runner can show a slot-reminder
    # hint in the status line. Cleared once booking succeeds.
    slots_shown: Optional[bool]

    # Structured slot list last shown to the patient, as
    # [{"label": str, "datetime": ISO str}]. Written by get_available_slots so the
    # UI can render a tappable picker and re-render it on history reload.
    offered_slots: Optional[list]

    # Per-doctor scheduling: chosen doctor for this booking turn.
    # None means use clinic-level schedule (single-doctor or any-doctor mode).
    doctor_id: Optional[int]

    # Populated by list_doctors tool; cleared after doctor is chosen.
    available_doctors: Optional[list]

    # IVR: populated by select_department tool when in hospital-routing mode.
    # Carries the department list so it is available in the prompt context.
    departments: Optional[list]

    # Populated by list_my_appointments (authenticated patients only) so
    # cancel/reschedule can resolve "appointment N" safely. Cleared after a
    # cancel/reschedule succeeds.
    my_appointments: Optional[list]

    # Most recent completed visit from patient memory (loaded with the prompt
    # context) — lets the UI offer a one-tap "book Dr. X again" chip.
    last_visit: Optional[dict]

    # Rolling Bangla summary of older, trimmed-away messages. Long threads are
    # compacted by call_model_node: old turns are summarized into this field and
    # removed from `messages`, keeping CPU prefill time and num_ctx bounded.
    conversation_summary: Optional[str]


def initial_state() -> AppointmentState:
    return AppointmentState(
        messages=[],
        clinic_id=None,
        hospital_id=None,
        channel=None,
        session_id=None,
        channel_identifier=None,
        patient_name=None,
        patient_age=None,
        patient_mobile=None,
        appointment_id=None,
    )
