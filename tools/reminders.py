"""24-hour appointment reminder scheduler.

Called every hour by the FastAPI lifespan task. Idempotent: reminder_sent_at
prevents double-sending even if called more than once in the 23–25 h window.
"""

from __future__ import annotations

import logging

from tools.database import (
    cancel_appointment_by_patient,
    confirm_appointment_by_patient,
    get_appointments_needing_reminder,
    get_reminded_appointment_by_phone,
    mark_reminder_sent,
)
from tools.sms import send_reminder
from utils.text import normalize_bangla_digits

log = logging.getLogger(__name__)

# Accepted reminder replies (after Bangla-digit normalisation + lowercase).
_CONFIRM_REPLIES = {"1", "হ্যাঁ", "হা", "জি", "yes"}
_CANCEL_REPLIES = {"2", "না", "no", "cancel", "বাতিল"}


async def handle_reminder_reply(phone: str, text: str) -> str | None:
    """Two-way reminder pre-router for inbound SMS/WhatsApp.

    If the sender has a reminded upcoming appointment and the message is a
    plain "১"/"২" style reply, confirm or cancel it deterministically (no LLM
    turn) and return the Bangla reply to send. Returns None when the message
    is not a reminder reply — the caller falls through to the normal agent.
    """
    token = normalize_bangla_digits((text or "").strip()).lower().rstrip("।.!")
    confirm = token in _CONFIRM_REPLIES
    cancel = token in _CANCEL_REPLIES
    if not (confirm or cancel):
        return None
    try:
        appt = await get_reminded_appointment_by_phone(phone)
    except Exception:
        log.exception("reminder-reply lookup failed for %s", phone)
        return None
    if appt is None:
        return None

    if confirm:
        if appt.get("patient_confirmed_at"):
            return "আপনার অ্যাপয়েন্টমেন্ট আগেই নিশ্চিত করা হয়েছে। ধন্যবাদ!"
        await confirm_appointment_by_patient(appt["id"])
        log.info("Reminder reply: appointment %s confirmed by patient", appt["id"])
        return "ধন্যবাদ! আপনার আগামীকালের অ্যাপয়েন্টমেন্ট নিশ্চিত করা হয়েছে।"

    ok = await cancel_appointment_by_patient(appt["id"])
    if not ok:
        return None
    log.info("Reminder reply: appointment %s cancelled by patient", appt["id"])
    return (
        "আপনার অ্যাপয়েন্টমেন্টটি বাতিল করা হয়েছে। নতুন সময় নিতে চাইলে "
        "যেকোনো সময় মেসেজ করুন।"
    )


async def send_pending_reminders() -> int:
    """Send reminder SMS for all appointments due in ~24 h. Returns count sent."""
    rows = await get_appointments_needing_reminder()
    sent = 0
    for row in rows:
        try:
            await send_reminder(
                row["patient_mobile"],
                row["patient_name"],
                row.get("slot_iso", str(row.get("scheduled_at", ""))),
                clinic_id=row.get("clinic_id"),
            )
            await mark_reminder_sent(row["id"])
            sent += 1
            log.info("Reminder sent for appointment %s (%s)", row["id"], row["patient_name"])
        except Exception:
            log.exception("Failed to send reminder for appointment %s", row["id"])
    return sent
