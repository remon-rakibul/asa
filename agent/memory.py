"""Cross-session patient memory (LangGraph PostgresStore).

One namespace per patient account: ``("patient_memory", str(account_id))``.
Key ``"profile"`` holds identity {name, age, phone, visit_count}; keys
``"visit:{appointment_id}"`` hold one booking each, with a Bangla ``summary``
that graph.py's store index embeds for semantic recall ("গতবারের ডাক্তার").

Writes are deterministic — after a successful booking (the agent's
post_booking_node, or the direct portal-booking endpoint) — never
LLM-extracted (gemma4 on CPU can't afford extraction calls). Reads happen
per turn in nodes._load_patient_context, rendered into the prompt TAIL so
the KV-cache-stable head is untouched.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from tools.database import (
    get_clinic,
    get_doctor,
    get_hospital,
    get_hospital_id_for_clinic,
)

log = logging.getLogger(__name__)


async def build_visit_record(
    *,
    clinic_id: Optional[int] = None,
    doctor_id: Optional[int] = None,
    slot_label: Optional[str] = None,
    serial_number: Optional[int] = None,
) -> dict:
    """Assemble one visit record, resolving names best-effort from the roster.

    Prefers the actually chosen doctor's name/specialty over the clinic's
    default doctor (a marketplace department can host several doctors).
    """
    department = hospital_name = doctor_name = specialty = ""
    try:
        cfg = (await get_clinic(clinic_id) or {}) if clinic_id else {}
        department = cfg.get("name") or ""
        doctor_name = cfg.get("doctor_name") or ""
        hospital_id = await get_hospital_id_for_clinic(clinic_id) if clinic_id else None
        if hospital_id:
            hosp = await get_hospital(hospital_id)
            hospital_name = (hosp or {}).get("name") or ""
        if doctor_id:
            doc = await get_doctor(doctor_id, clinic_id=clinic_id)
            if doc:
                doctor_name = doc.get("name") or doctor_name
                specialty = doc.get("specialty") or ""
    except Exception as exc:
        log.debug("visit record enrichment failed: %s", exc)

    has_honorific = bool(re.match(r"^\s*(dr\.?\s|ডা\.|ডাঃ|ডঃ)", doctor_name, re.IGNORECASE))
    doctor_part = doctor_name if has_honorific else (f"ডা. {doctor_name}" if doctor_name else "")
    if doctor_part and specialty:
        doctor_part += f" ({specialty})"
    parts = [p for p in (department, hospital_name, doctor_part, slot_label or "") if p]
    return {
        "clinic_id": clinic_id,
        "doctor_id": doctor_id,
        "slot_label": slot_label,
        "serial_number": serial_number,
        "department_name": department or None,
        "hospital_name": hospital_name or None,
        "doctor_name": doctor_name or None,
        "specialty": specialty or None,
        "summary": "ভিজিট: " + " — ".join(parts) if parts else None,
    }


async def write_patient_memory(
    store,
    *,
    account_id: int,
    name: Optional[str] = None,
    age: Optional[int] = None,
    phone: Optional[str] = None,
    appointment_id: Optional[str] = None,
    visit: Optional[dict] = None,
) -> None:
    """Upsert the patient's profile and (optionally) append one visit record.

    Profile values merge on top of existing ones; ``visit_count`` increments
    once per NEW visit key (re-writing the same appointment doesn't double
    count). No-op when store or account_id is missing.
    """
    if store is None or not account_id:
        return

    namespace = ("patient_memory", str(account_id))

    profile_item = await store.aget(namespace, "profile")
    profile: dict = dict(profile_item.value) if profile_item else {}
    if name:
        profile["name"] = name
    if age:
        profile["age"] = age
    if phone:
        profile["phone"] = phone

    new_visit = False
    if appointment_id and visit:
        visit_key = f"visit:{appointment_id}"
        new_visit = await store.aget(namespace, visit_key) is None
        await store.aput(namespace, visit_key, visit)

    if new_visit:
        profile["visit_count"] = int(profile.get("visit_count") or 0) + 1
    if profile:
        await store.aput(namespace, "profile", profile)
