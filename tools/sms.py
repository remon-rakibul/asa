"""Outbound SMS helpers with a pluggable provider.

Provider is chosen by settings.sms_provider ("twilio" | "bdgateway" | "none").
All sends are no-ops when the selected provider is not configured, so the agent
runs without SMS credentials during development. Message bodies are in Bangla,
editable per department (clinics.sms_templates) with a hardcoded default, and
branded per clinic when a clinic_id is supplied.

Every send — sent, failed, or skipped (no provider) — is recorded in `sms_log`
via tools.database.log_sms so the console can show SMS tracking.
"""

from __future__ import annotations

import asyncio
import json
import logging
from functools import lru_cache

import httpx

from config import settings
from tools.database import charge_channel_usage, get_clinic, log_sms

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Editable message templates (Bangla)
# --------------------------------------------------------------------------- #
# Placeholders are filled with str.format_map; unknown placeholders are left
# verbatim (see _safe_format) so an edited template can never crash a send.

DEFAULT_TEMPLATES: dict[str, str] = {
    "confirmation": (
        "প্রিয় {patient_name},\n"
        "{clinic} এ আপনার অ্যাপয়েন্টমেন্ট নিশ্চিত হয়েছে।\n"
        "ডাক্তার: {doctor}\n"
        "সময়: {slot}\n"
        "সিরিয়াল নম্বর: {serial}\n"
        "অনুগ্রহ করে নির্ধারিত সময়ের ৫ মিনিট আগে আসবেন।"
    ),
    "reminder": (
        "স্মরণ করিয়ে দিচ্ছি: {patient_name}, আগামীকাল {clinic} এ {doctor}-এর "
        "সাথে আপনার অ্যাপয়েন্টমেন্ট রয়েছে — {slot}। "
        "উত্তর দিন: ১ = নিশ্চিত, ২ = বাতিল।"
    ),
    "doctor_alert": (
        "{clinic} এ নতুন বুকিং: {patient_name}, বয়স {age}, "
        "মোবাইল {mobile} — {slot}।"
    ),
    "token": (
        "{patient_name}, আপনার সিরিয়াল নম্বর {token} এখন ডাকা হচ্ছে। "
        "অনুগ্রহ করে কাউন্টারে আসুন।"
    ),
}

# Placeholders each template understands — surfaced in the Settings UI help.
TEMPLATE_PLACEHOLDERS: dict[str, list[str]] = {
    "confirmation": ["patient_name", "clinic", "doctor", "slot", "serial"],
    "reminder": ["patient_name", "clinic", "doctor", "slot"],
    "doctor_alert": ["clinic", "patient_name", "age", "mobile", "slot"],
    "token": ["patient_name", "token", "clinic"],
}


class _SafeDict(dict):
    """format_map helper: leaves unknown {placeholders} untouched."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _safe_format(template: str, values: dict) -> str:
    try:
        return template.format_map(_SafeDict(values))
    except Exception:
        # A malformed template (e.g. stray brace) should never break a send.
        return template


def _render(kind: str, templates: dict, **values) -> str:
    tpl = (templates.get(kind) or "").strip() or DEFAULT_TEMPLATES[kind]
    return _safe_format(tpl, values)


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #

@lru_cache(maxsize=1)
def _twilio_client():
    from twilio.rest import Client
    return Client(settings.twilio_account_sid, settings.twilio_auth_token)


def _twilio_configured() -> bool:
    return bool(
        settings.twilio_account_sid
        and settings.twilio_auth_token
        and settings.twilio_from_number
    )


def _twilio_send(to: str, body: str, from_: str) -> None:
    _twilio_client().messages.create(to=to, from_=from_, body=body)


def _bdgateway_configured() -> bool:
    return bool(settings.bd_sms_api_url and settings.bd_sms_api_key)


async def _bdgateway_send(to: str, body: str, sender: str) -> None:
    """POST to a generic BD HTTP SMS gateway (e.g. SSL Wireless). The exact field
    names vary per vendor; adjust here once the gateway contract is known."""
    payload = {
        "api_key": settings.bd_sms_api_key,
        "sender_id": sender,
        "to": to,
        "msg": body,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(settings.bd_sms_api_url, data=payload)
        if resp.status_code >= 400:
            raise RuntimeError(f"BD SMS gateway {resp.status_code}: {resp.text[:200]}")


async def _dispatch(
    to: str, body: str, *, kind: str, clinic_id: int | None, sender_id: str = "",
) -> None:
    """Send via the configured provider and record the result in sms_log.

    Status is `sent`, `failed`, or `skipped` (no provider configured). The
    per-clinic sender_id overrides the global From/sender when set.
    """
    provider = settings.sms_provider
    status = "skipped"
    used_provider = "none"
    err: str | None = None
    try:
        if provider == "twilio" and _twilio_configured():
            from_ = sender_id or settings.twilio_from_number
            await asyncio.to_thread(_twilio_send, to, body, from_)
            status, used_provider = "sent", "twilio"
        elif provider == "bdgateway" and _bdgateway_configured():
            sender = sender_id or settings.bd_sms_sender_id
            await _bdgateway_send(to, body, sender)
            status, used_provider = "sent", "bdgateway"
        # else: provider "none" or unconfigured -> skipped
    except Exception as exc:
        status, err = "failed", str(exc)[:500]
        log.exception("Failed to send SMS to %s", to)

    await log_sms(
        clinic_id=clinic_id, to_number=to, body=body, kind=kind,
        status=status, provider=used_provider, error=err,
    )
    # Meter a genuinely-sent SMS against the hospital wallet (no-op when credits
    # are off, on a skip/fail, or for platform SMS with no clinic). Fail-open.
    if status == "sent":
        await charge_channel_usage(
            clinic_id, reason="sms", credits=settings.credit_cost_sms, note=kind,
        )


async def send_sms(to: str, body: str, clinic_id: int | None = None) -> None:
    """Send an arbitrary outbound SMS (e.g. agent conversational reply via the
    Twilio SMS webhook), recorded with kind 'agent_reply'."""
    c = await _clinic_for_sms(clinic_id)
    await _dispatch(to, body, kind="agent_reply", clinic_id=clinic_id, sender_id=c["sender_id"])


async def resend(to: str, body: str, kind: str, clinic_id: int | None = None) -> None:
    """Re-send a previously logged SMS verbatim (used by the Messages 'Resend'
    action). Logs a fresh sms_log row with the original kind."""
    c = await _clinic_for_sms(clinic_id)
    await _dispatch(to, body, kind=kind, clinic_id=clinic_id, sender_id=c["sender_id"])


# --------------------------------------------------------------------------- #
# Per-clinic branding, sender, and templates
# --------------------------------------------------------------------------- #

def _parse_templates(raw) -> dict:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return {}
    return raw if isinstance(raw, dict) else {}


async def _clinic_for_sms(clinic_id: int | None) -> dict:
    """Branding + sender + templates for a clinic, falling back to env defaults."""
    if clinic_id is not None:
        cfg = await get_clinic(clinic_id)
        if cfg:
            return {
                "clinic": cfg["name"],
                "doctor": cfg["doctor_name"],
                "doctor_phone": cfg["doctor_phone"],
                "sender_id": (cfg.get("sms_sender_id") or "").strip(),
                "templates": _parse_templates(cfg.get("sms_templates")),
            }
    return {
        "clinic": settings.clinic_name,
        "doctor": settings.doctor_name,
        "doctor_phone": settings.doctor_phone,
        "sender_id": "",
        "templates": {},
    }


# --------------------------------------------------------------------------- #
# Messages (Bangla)
# --------------------------------------------------------------------------- #

async def send_booking_confirmation(
    patient_mobile: str,
    patient_name: str,
    slot_label: str,
    clinic_id: int | None = None,
    serial_number: int | None = None,
    doctor_name: str | None = None,
) -> None:
    from utils.text import to_bangla_digits
    c = await _clinic_for_sms(clinic_id)
    doc = doctor_name or c["doctor"]
    serial = to_bangla_digits(serial_number) if serial_number else ""
    body = _render(
        "confirmation", c["templates"],
        patient_name=patient_name, clinic=c["clinic"], doctor=doc,
        slot=slot_label, serial=serial,
    )
    await _dispatch(
        patient_mobile, body, kind="confirmation",
        clinic_id=clinic_id, sender_id=c["sender_id"],
    )


async def send_doctor_notification(
    patient_name: str, patient_age: int, patient_mobile: str, slot_label: str,
    clinic_id: int | None = None,
) -> None:
    c = await _clinic_for_sms(clinic_id)
    if not c["doctor_phone"]:
        return
    body = _render(
        "doctor_alert", c["templates"],
        clinic=c["clinic"], patient_name=patient_name, age=patient_age,
        mobile=patient_mobile, slot=slot_label,
    )
    await _dispatch(
        c["doctor_phone"], body, kind="doctor_alert",
        clinic_id=clinic_id, sender_id=c["sender_id"],
    )


async def send_reminder(
    patient_mobile: str, patient_name: str, slot_label: str,
    clinic_id: int | None = None,
) -> None:
    c = await _clinic_for_sms(clinic_id)
    body = _render(
        "reminder", c["templates"],
        patient_name=patient_name, clinic=c["clinic"], doctor=c["doctor"], slot=slot_label,
    )
    await _dispatch(
        patient_mobile, body, kind="reminder",
        clinic_id=clinic_id, sender_id=c["sender_id"],
    )


async def send_token_notification(
    patient_mobile: str, patient_name: str, token_label: str,
    clinic_id: int | None = None,
) -> None:
    """Notify a patient that their token number is being called."""
    c = await _clinic_for_sms(clinic_id)
    body = _render(
        "token", c["templates"],
        patient_name=patient_name, token=token_label, clinic=c["clinic"],
    )
    await _dispatch(
        patient_mobile, body, kind="token",
        clinic_id=clinic_id, sender_id=c["sender_id"],
    )
