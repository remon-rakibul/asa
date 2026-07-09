"""Postgres data access for the appointment agent.

Holds a shared asyncpg pool and the two operations the graph needs:
  - get_available_slots(): free slots = schedule minus confirmed bookings
  - book_appointment(): insert a confirmed appointment row
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, time, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import asyncio

import asyncpg

from config import settings
from tools.crypto import decrypt_field, encrypt_field

log = logging.getLogger(__name__)


def _resolve_tz(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name or "UTC")
    except (ZoneInfoNotFoundError, KeyError):
        log.warning("Unknown timezone '%s', falling back to UTC", tz_name)
        return ZoneInfo("UTC")

# Spoken labels for weekdays (Bangla). Index matches Python weekday(): Mon=0.
_BANGLA_WEEKDAYS = [
    "সোমবার", "মঙ্গলবার", "বুধবার", "বৃহস্পতিবার",
    "শুক্রবার", "শনিবার", "রবিবার",
]

_pool: Optional[asyncpg.Pool] = None
_pool_lock: Optional[asyncio.Lock] = None


async def get_pool() -> asyncpg.Pool:
    """Lazily create and return the shared connection pool."""
    global _pool, _pool_lock
    # Lock is created synchronously (no await), so no two coroutines can race here.
    if _pool_lock is None:
        _pool_lock = asyncio.Lock()
    async with _pool_lock:
        if _pool is None:
            _pool = await asyncpg.create_pool(
                dsn=settings.database_url,
                min_size=settings.db_pool_min,
                max_size=settings.db_pool_max,
            )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def terminate_pool() -> None:
    """Force-close the pool without waiting for connections to be released.

    Shutdown fallback when close_pool() can't drain (e.g. a still-open SSE
    stream holds a connection) — lets uvicorn reloads/shutdowns finish.
    """
    global _pool
    if _pool is not None:
        _pool.terminate()
        _pool = None


def _format_label(dt: datetime) -> str:
    """Human/voice-friendly Bangla label, e.g. 'সোমবার সকাল ৯টা'."""
    weekday = _BANGLA_WEEKDAYS[dt.weekday()]
    hour = dt.hour
    minute = dt.minute
    # Coarse part-of-day in Bangla.
    if hour < 12:
        part = "সকাল"
    elif hour < 16:
        part = "দুপুর"
    elif hour < 19:
        part = "বিকেল"
    else:
        part = "সন্ধ্যা"
    h12 = hour % 12 or 12
    time_str = f"{h12}টা" if minute == 0 else f"{h12}টা {minute} মিনিট"
    return f"{weekday} {part} {time_str}"


async def get_doctors_for_clinic(clinic_id: int) -> list[dict]:
    """Return all active doctors for a clinic, ordered by is_primary desc then id."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, name, specialty, degrees, description, phone, is_primary, "
            "(photo IS NOT NULL) AS has_photo "
            "FROM doctors WHERE clinic_id = $1 ORDER BY is_primary DESC, id",
            clinic_id,
        )
    return [dict(r) for r in rows]


async def get_doctor(doctor_id: int, clinic_id: Optional[int] = None) -> Optional[dict]:
    """One doctor row by id, optionally scoped to clinic_id (ownership check).

    Used to validate a client-supplied doctor_id actually belongs to the
    clinic before trusting it, and to fetch the doctor's name for prompt
    context. Returns None if not found, or if clinic_id is given and doesn't
    match (so the caller can silently drop a mismatched id).
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        if clinic_id is not None:
            row = await conn.fetchrow(
                "SELECT id, name, specialty, degrees, description, phone, is_primary, "
                "(photo IS NOT NULL) AS has_photo FROM doctors "
                "WHERE id = $1 AND clinic_id = $2",
                doctor_id, clinic_id,
            )
        else:
            row = await conn.fetchrow(
                "SELECT id, clinic_id, name, specialty, degrees, description, phone, "
                "is_primary, (photo IS NOT NULL) AS has_photo FROM doctors "
                "WHERE id = $1",
                doctor_id,
            )
    return dict(row) if row else None


async def get_available_slots(
    clinic_id: int,
    days_ahead: int = None,
    limit: Optional[int] = None,
    doctor_id: Optional[int] = None,
) -> list[dict]:
    """Return open appointment slots for a clinic over the next `days_ahead` days.

    When doctor_id is given, only slots from that doctor's schedule are returned
    and only that doctor's existing appointments are subtracted.
    When doctor_id is None, falls back to clinic-level schedule (doctor_id IS NULL rows).

    Each slot: {"datetime": <iso str>, "label": <bangla spoken label>}.
    """
    days_ahead = days_ahead or settings.availability_days_ahead
    pool = await get_pool()

    async with pool.acquire() as conn:
        tz_row = await conn.fetchrow(
            "SELECT timezone FROM clinics WHERE id = $1", clinic_id
        )
        tz = _resolve_tz((tz_row["timezone"] if tz_row else None) or "UTC")
        now = datetime.now(tz)
        window_end = now + timedelta(days=days_ahead)

        if doctor_id is not None:
            schedule_rows = await conn.fetch(
                "SELECT day_of_week, start_time, end_time, slot_duration "
                "FROM doctor_schedule WHERE clinic_id = $1 AND doctor_id = $2",
                clinic_id, doctor_id,
            )
            if schedule_rows:
                booked_rows = await conn.fetch(
                    "SELECT scheduled_at FROM appointments "
                    "WHERE clinic_id = $1 AND doctor_id = $2 AND status = 'confirmed' "
                    "AND scheduled_at >= $3 AND scheduled_at < $4",
                    clinic_id, doctor_id, now, window_end,
                )
            else:
                # No dedicated schedule for this doctor — most departments only
                # configure clinic-level hours (doctor_id IS NULL), and the
                # portal wizard still pre-selects the doctor. Fall through to
                # the clinic-wide schedule (with clinic-wide booked slots
                # subtracted) instead of reporting zero availability.
                doctor_id = None
        if doctor_id is None:
            # Clinic-level schedule: rows where doctor_id IS NULL
            schedule_rows = await conn.fetch(
                "SELECT day_of_week, start_time, end_time, slot_duration "
                "FROM doctor_schedule WHERE clinic_id = $1 AND doctor_id IS NULL",
                clinic_id,
            )
            if not schedule_rows:
                # Fallback: any schedule row for the clinic (old single-doctor data)
                schedule_rows = await conn.fetch(
                    "SELECT day_of_week, start_time, end_time, slot_duration "
                    "FROM doctor_schedule WHERE clinic_id = $1",
                    clinic_id,
                )
            booked_rows = await conn.fetch(
                "SELECT scheduled_at FROM appointments "
                "WHERE clinic_id = $1 AND status = 'confirmed' "
                "AND scheduled_at >= $2 AND scheduled_at < $3",
                clinic_id, now, window_end,
            )

    schedule_by_day = {r["day_of_week"]: r for r in schedule_rows}
    booked = {r["scheduled_at"].astimezone(tz) for r in booked_rows}

    slots: list[dict] = []
    for offset in range(days_ahead):
        day = (now + timedelta(days=offset)).date()
        weekday = day.weekday()
        row = schedule_by_day.get(weekday)
        if row is None:
            continue

        cursor = datetime.combine(day, row["start_time"], tzinfo=tz)
        day_end = datetime.combine(day, row["end_time"], tzinfo=tz)
        step = timedelta(minutes=row["slot_duration"])

        while cursor + step <= day_end:
            if cursor > now and cursor not in booked:
                slots.append(
                    {"datetime": cursor.isoformat(), "label": _format_label(cursor)}
                )
            cursor += step

    slots.sort(key=lambda s: s["datetime"])
    return slots[:limit] if limit else slots


async def book_appointment(
    *,
    clinic_id: int,
    patient_name: str,
    patient_age: int,
    patient_mobile: str,
    scheduled_at: str,
    duration_mins: int = 30,
    patient_id: Optional[int] = None,
    doctor_id: Optional[int] = None,
    appointment_type: str = "opd",
    consent_at: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Optional[dict]:
    """Insert a confirmed appointment. Patient name and mobile are encrypted when
    PATIENT_ENCRYPTION_KEY is set. Returns the new UUID, or None on race-loss.

    Optional hospital-mode fields (patient_id, doctor_id, appointment_type,
    consent_at) require migration 0007 columns; they are silently ignored if
    those columns do not yet exist.

    session_id (migration 0010) links the appointment back to the conversation
    that booked it; silently ignored if the column does not yet exist.
    """
    pool = await get_pool()
    try:
        dt = datetime.fromisoformat(scheduled_at)
    except (ValueError, TypeError):
        return None
    consent_dt: Optional[datetime] = None
    if consent_at:
        try:
            consent_dt = datetime.fromisoformat(consent_at)
        except (ValueError, TypeError):
            pass
    try:
        async with pool.acquire() as conn:
            try:
                row = await conn.fetchrow(
                    "INSERT INTO appointments "
                    "(clinic_id, patient_name, patient_age, patient_mobile, "
                    " scheduled_at, duration_mins, patient_id, doctor_id, "
                    " appointment_type, consent_at, session_id) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11) RETURNING id",
                    clinic_id,
                    encrypt_field(patient_name),
                    patient_age,
                    encrypt_field(patient_mobile),
                    dt, duration_mins,
                    patient_id, doctor_id, appointment_type, consent_dt,
                    session_id,
                )
            except asyncpg.UndefinedColumnError:
                # session_id column (0010) not yet migrated — try without it.
                try:
                    row = await conn.fetchrow(
                        "INSERT INTO appointments "
                        "(clinic_id, patient_name, patient_age, patient_mobile, "
                        " scheduled_at, duration_mins, patient_id, doctor_id, "
                        " appointment_type, consent_at) "
                        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) RETURNING id",
                        clinic_id,
                        encrypt_field(patient_name),
                        patient_age,
                        encrypt_field(patient_mobile),
                        dt, duration_mins,
                        patient_id, doctor_id, appointment_type, consent_dt,
                    )
                except asyncpg.UndefinedColumnError:
                    # Hospital-mode columns not yet in schema — fall back to base columns.
                    row = await conn.fetchrow(
                        "INSERT INTO appointments "
                        "(clinic_id, patient_name, patient_age, patient_mobile, scheduled_at, duration_mins) "
                        "VALUES ($1, $2, $3, $4, $5, $6) RETURNING id",
                        clinic_id,
                        encrypt_field(patient_name),
                        patient_age,
                        encrypt_field(patient_mobile),
                        dt, duration_mins,
                    )
            # Assign serial number atomically: advisory lock prevents concurrent
            # transactions from reading the same MAX and assigning duplicate serials.
            serial_number: Optional[int] = None
            try:
                async with conn.transaction():
                    lock_key = clinic_id * 1_000_000 + (dt.toordinal() % 1_000_000)
                    await conn.execute("SELECT pg_advisory_xact_lock($1)", lock_key)
                    max_serial = await conn.fetchval(
                        "SELECT COALESCE(MAX(serial_number), 0) FROM appointments "
                        "WHERE clinic_id = $1 AND scheduled_at::date = $2::date",
                        clinic_id, dt,
                    )
                    serial_number = (max_serial or 0) + 1
                    await conn.execute(
                        "UPDATE appointments SET serial_number = $1 WHERE id = $2",
                        serial_number, row["id"],
                    )
            except asyncpg.UndefinedColumnError:
                pass  # migration 0011 not yet applied
            new_id = str(row["id"])
        # Record the creation event outside the booking connection (best-effort).
        await _record_appointment_event(
            appointment_id=new_id, clinic_id=clinic_id,
            event_type="created", to_status="confirmed", to_time=dt,
            actor_role="agent" if session_id else "",
        )
        return {"id": new_id, "serial_number": serial_number}
    except asyncpg.UniqueViolationError:
        return None


# --------------------------------------------------------------------------- #
# Management queries (used by the FastAPI REST API / admin clients)
# --------------------------------------------------------------------------- #

async def list_appointments(
    *,
    clinic_id: int,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """List a clinic's appointments with optional filters and pagination.

    When encryption is active the `q` search filter is applied after decryption
    (ILIKE on ciphertext would never match). Date/status filters still run in SQL.
    """
    pool = await get_pool()
    params: list = []

    def _p(value) -> str:
        params.append(value)
        return f"${len(params)}"

    conditions: list[str] = [f"clinic_id = {_p(clinic_id)}"]

    # asyncpg binds date/timestamp params as date objects, not strings.
    if date_from:
        conditions.append(f"scheduled_at >= {_p(date.fromisoformat(date_from))}")
    if date_to:
        conditions.append(
            f"scheduled_at < {_p(date.fromisoformat(date_to) + timedelta(days=1))}"
        )
    if status and status != "all":
        conditions.append(f"status = {_p(status)}")
    # When encryption is off, push q to SQL for efficiency.
    from tools.crypto import _get_fernet
    encrypt_active = _get_fernet() is not None
    if q and not encrypt_active:
        like = _p(f"%{q}%")
        conditions.append(f"(patient_name ILIKE {like} OR patient_mobile ILIKE {like})")

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.extend([int(limit), int(offset)])
    lim_n, off_n = len(params) - 1, len(params)
    _tail = f"FROM appointments {where} ORDER BY scheduled_at DESC LIMIT ${lim_n} OFFSET ${off_n}"
    async with pool.acquire() as conn:
        try:
            rows = await conn.fetch(
                f"SELECT id::text AS id, patient_name, patient_age, patient_mobile, "
                f"scheduled_at, duration_mins, status, created_at, serial_number, "
                f"doctor_id, patient_confirmed_at {_tail}",
                *params,
            )
        except asyncpg.UndefinedColumnError:
            rows = await conn.fetch(
                f"SELECT id::text AS id, patient_name, patient_age, patient_mobile, "
                f"scheduled_at, duration_mins, status, created_at {_tail}",
                *params,
            )

    results = [
        {**dict(r),
         "patient_name": decrypt_field(r["patient_name"]),
         "patient_mobile": decrypt_field(r["patient_mobile"])}
        for r in rows
    ]

    # Apply plaintext search after decryption when encryption is active.
    if q and encrypt_active:
        ql = q.lower()
        results = [
            r for r in results
            if ql in (r.get("patient_name") or "").lower()
            or ql in (r.get("patient_mobile") or "").lower()
        ]

    return results


async def cancel_appointment(
    clinic_id: int, appointment_id: str, *,
    actor_user_id: Optional[int] = None, actor_role: str = "",
    reason: Optional[str] = None,
) -> bool:
    """Cancel a confirmed or checked-in appointment owned by the clinic.

    Stamps cancelled_at / updated_by, records a `cancelled` history event, and
    returns True if a row changed.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE appointments "
            "SET status = 'cancelled', cancelled_at = now(), updated_at = now(), "
            "    updated_by = $3, cancel_reason = COALESCE($4, cancel_reason) "
            "WHERE id = $1::uuid AND clinic_id = $2 "
            "      AND status IN ('confirmed', 'checked_in') "
            "RETURNING status AS new_status",
            appointment_id, clinic_id, actor_user_id, reason,
        )
    if row is None:
        return False
    await _record_appointment_event(
        appointment_id=appointment_id, clinic_id=clinic_id,
        event_type="cancelled", to_status="cancelled",
        actor_user_id=actor_user_id, actor_role=actor_role, note=reason,
    )
    return True


async def reschedule_appointment(
    clinic_id: int, appointment_id: str, new_slot_iso: str, *,
    actor_user_id: Optional[int] = None, actor_role: str = "",
) -> dict:
    """Move a confirmed appointment to a new time within the same clinic.

    Returns {"status": "ok"|"slot_taken"|"not_found", "appointment": {...}|None}.
    The slot is rejected if another confirmed appointment already holds it. The
    serial number is preserved, and a `rescheduled` history event captures the
    old -> new time.
    """
    try:
        dt = datetime.fromisoformat(new_slot_iso)
    except (ValueError, TypeError):
        return {"status": "not_found", "appointment": None}

    pool = await get_pool()
    async with pool.acquire() as conn:
        clash = await conn.fetchval(
            "SELECT 1 FROM appointments "
            "WHERE clinic_id = $1 AND scheduled_at = $2 AND status = 'confirmed' "
            "AND id <> $3::uuid",
            clinic_id, dt, appointment_id,
        )
        if clash:
            return {"status": "slot_taken", "appointment": None}

        old_time = await conn.fetchval(
            "SELECT scheduled_at FROM appointments "
            "WHERE id = $1::uuid AND clinic_id = $2 AND status = 'confirmed'",
            appointment_id, clinic_id,
        )
        try:
            row = await conn.fetchrow(
                "UPDATE appointments SET scheduled_at = $1, updated_at = now(), "
                "    updated_by = $4 "
                "WHERE id = $2::uuid AND clinic_id = $3 AND status = 'confirmed' "
                "RETURNING id::text AS id, patient_name, patient_mobile, scheduled_at, "
                "serial_number, doctor_id, clinic_id",
                dt, appointment_id, clinic_id, actor_user_id,
            )
        except asyncpg.UniqueViolationError:
            # Lost the race: another reschedule/booking took this slot between
            # our clash check and this UPDATE (uq_confirmed_slot). Same clean
            # outcome as the pre-check catching it, not an unhandled 500.
            return {"status": "slot_taken", "appointment": None}
    if not row:
        return {"status": "not_found", "appointment": None}
    await _record_appointment_event(
        appointment_id=appointment_id, clinic_id=clinic_id,
        event_type="rescheduled", from_time=old_time, to_time=dt,
        actor_user_id=actor_user_id, actor_role=actor_role,
    )
    appt = {**dict(row),
            "patient_name": decrypt_field(row["patient_name"]),
            "patient_mobile": decrypt_field(row["patient_mobile"])}
    return {"status": "ok", "appointment": appt}


async def reschedule_appointment_for_account(
    account_id: int, appointment_id: str, new_slot_iso: str
) -> dict:
    """Reschedule one of the patient account's own confirmed appointments.

    Verifies ownership (appointments.patient_id -> patients.account_id), then
    delegates to reschedule_appointment with the resolved clinic_id.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        clinic_id = await conn.fetchval(
            "SELECT a.clinic_id FROM appointments a JOIN patients p ON a.patient_id = p.id "
            "WHERE a.id = $1::uuid AND p.account_id = $2 AND a.status = 'confirmed'",
            appointment_id, account_id,
        )
    if clinic_id is None:
        return {"status": "not_found", "appointment": None}
    return await reschedule_appointment(clinic_id, appointment_id, new_slot_iso)


# --------------------------------------------------------------------------- #
# Appointment lifecycle: status transitions + change-history events
# --------------------------------------------------------------------------- #

# Allowed status transitions. Terminal states (completed/no_show/cancelled) have
# no outgoing edges.
_STATUS_TRANSITIONS: dict[str, set[str]] = {
    "confirmed": {"checked_in", "completed", "no_show", "cancelled"},
    "checked_in": {"completed", "no_show", "cancelled"},
    "completed": set(),
    "no_show": set(),
    "cancelled": set(),
}

# Status -> the timestamp column stamped when an appointment enters it.
_STATUS_TIMESTAMP: dict[str, str] = {
    "checked_in": "checked_in_at",
    "completed": "completed_at",
    "cancelled": "cancelled_at",
}


async def _record_appointment_event(
    *,
    appointment_id: str,
    clinic_id: Optional[int],
    event_type: str,
    from_status: Optional[str] = None,
    to_status: Optional[str] = None,
    from_time: Optional[datetime] = None,
    to_time: Optional[datetime] = None,
    actor_user_id: Optional[int] = None,
    actor_role: str = "",
    note: Optional[str] = None,
) -> None:
    """Best-effort append to appointment_events. Never raises (mirrors audit)."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO appointment_events "
                "(appointment_id, clinic_id, event_type, from_status, to_status, "
                " from_time, to_time, actor_user_id, actor_role, note) "
                "VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10)",
                appointment_id, clinic_id, event_type, from_status, to_status,
                from_time, to_time, actor_user_id, actor_role, note,
            )
    except Exception:
        log.warning("appointment_events insert failed", exc_info=True)


async def set_appointment_status(
    clinic_id: int, appointment_id: str, new_status: str, *,
    actor_user_id: Optional[int] = None, actor_role: str = "",
    reason: Optional[str] = None,
) -> dict:
    """Transition an appointment to a new lifecycle status with validation.

    Returns {"status": "ok"|"invalid"|"not_found", "appointment": {...}|None,
             "from": <current status>|None}.
    "invalid" covers both unknown target statuses and illegal transitions.
    """
    if new_status not in _STATUS_TRANSITIONS:
        return {"status": "invalid", "appointment": None, "from": None}

    pool = await get_pool()
    async with pool.acquire() as conn:
        current = await conn.fetchval(
            "SELECT status FROM appointments WHERE id = $1::uuid AND clinic_id = $2",
            appointment_id, clinic_id,
        )
        if current is None:
            return {"status": "not_found", "appointment": None, "from": None}
        if new_status not in _STATUS_TRANSITIONS.get(current, set()):
            return {"status": "invalid", "appointment": None, "from": current}

        params: list = []

        def _p(value) -> str:
            params.append(value)
            return f"${len(params)}"

        sets = [
            f"status = {_p(new_status)}",
            "updated_at = now()",
            f"updated_by = {_p(actor_user_id)}",
        ]
        ts_col = _STATUS_TIMESTAMP.get(new_status)
        if ts_col:
            sets.append(f"{ts_col} = now()")
        if new_status == "cancelled" and reason:
            sets.append(f"cancel_reason = {_p(reason)}")
        where = f"id = {_p(appointment_id)}::uuid AND clinic_id = {_p(clinic_id)}"
        row = await conn.fetchrow(
            f"UPDATE appointments SET {', '.join(sets)} WHERE {where} "
            "RETURNING id::text AS id, patient_name, patient_mobile, scheduled_at, "
            "status, serial_number, doctor_id",
            *params,
        )

    await _record_appointment_event(
        appointment_id=appointment_id, clinic_id=clinic_id,
        event_type=new_status, from_status=current, to_status=new_status,
        actor_user_id=actor_user_id, actor_role=actor_role, note=reason,
    )
    appt = {**dict(row),
            "patient_name": decrypt_field(row["patient_name"]),
            "patient_mobile": decrypt_field(row["patient_mobile"])}
    return {"status": "ok", "appointment": appt, "from": current}


async def list_appointment_events(clinic_id: int, appointment_id: str) -> list[dict]:
    """Return the change-history timeline for one appointment (newest first).

    Joins users.email so the console can label the actor; agent/patient-driven
    events have a NULL actor and fall back to actor_role.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT e.id, e.event_type, e.from_status, e.to_status, "
            "       e.from_time, e.to_time, e.actor_user_id, e.actor_role, "
            "       e.note, e.created_at, u.email AS actor_email "
            "FROM appointment_events e "
            "LEFT JOIN users u ON u.id = e.actor_user_id "
            "WHERE e.appointment_id = $1::uuid AND e.clinic_id = $2 "
            "ORDER BY e.created_at DESC, e.id DESC",
            appointment_id, clinic_id,
        )
    return [dict(r) for r in rows]


async def get_appointments_needing_reminder() -> list[dict]:
    """Confirmed appointments due in ~24 h with no reminder sent, across all
    clinics. Each row carries its clinic's branding so the SMS can be addressed
    correctly per tenant."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT a.id::text AS id, a.clinic_id, a.patient_name, a.patient_mobile, "
            "a.scheduled_at, "
            "to_char(a.scheduled_at AT TIME ZONE c.timezone, 'DD Mon YYYY HH24:MI') AS slot_iso, "
            "c.name AS clinic_name, c.doctor_name "
            "FROM appointments a JOIN clinics c ON c.id = a.clinic_id "
            "WHERE a.status = 'confirmed' "
            "AND a.reminder_sent_at IS NULL "
            "AND a.scheduled_at BETWEEN now() + INTERVAL '23 hours' "
            "                       AND now() + INTERVAL '25 hours'"
        )
    return [
        {**dict(r),
         "patient_name": decrypt_field(r["patient_name"]),
         "patient_mobile": decrypt_field(r["patient_mobile"])}
        for r in rows
    ]


async def mark_reminder_sent(appointment_id: str) -> None:
    """Record that the reminder SMS was dispatched for this appointment."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE appointments SET reminder_sent_at = now() WHERE id = $1::uuid",
            appointment_id,
        )


async def get_schedule(clinic_id: int) -> list[dict]:
    """Return a clinic's weekly schedule rows, ordered by day."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, day_of_week, "
            "to_char(start_time, 'HH24:MI') AS start_time, "
            "to_char(end_time, 'HH24:MI') AS end_time, "
            "slot_duration FROM doctor_schedule WHERE clinic_id = $1 ORDER BY day_of_week",
            clinic_id,
        )
    return [dict(r) for r in rows]


def _parse_time(value) -> time:
    """Accept 'HH:MM' / 'HH:MM:SS' strings or datetime.time -> datetime.time."""
    if isinstance(value, time):
        return value
    parts = [int(p) for p in str(value).split(":")[:3]]
    while len(parts) < 3:
        parts.append(0)
    return time(parts[0], parts[1], parts[2])


async def save_schedule(clinic_id: int, rows: list[dict]) -> None:
    """Atomically replace a clinic's entire weekly schedule with the given rows."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM doctor_schedule WHERE clinic_id = $1", clinic_id)
            for r in rows:
                await conn.execute(
                    "INSERT INTO doctor_schedule "
                    "(clinic_id, day_of_week, start_time, end_time, slot_duration) "
                    "VALUES ($1, $2, $3, $4, $5)",
                    clinic_id,
                    int(r["day_of_week"]),
                    _parse_time(r["start_time"]),
                    _parse_time(r["end_time"]),
                    int(r["slot_duration"]),
                )


# --------------------------------------------------------------------------- #
# Tenancy: clinics, channel resolution, admin users
# --------------------------------------------------------------------------- #

_CLINIC_COLS = (
    "id, slug, name, doctor_name, doctor_phone, timezone, "
    "availability_days_ahead, status, greeting_instructions, "
    "sms_sender_id, sms_templates"
)

_CHANNEL_COLS = "id, clinic_id, hospital_id, kind, identifier, label, created_at"

# Fields an authenticated admin may edit on their own clinic.
_CLINIC_EDITABLE = (
    "name", "doctor_name", "doctor_phone", "availability_days_ahead",
    "timezone", "greeting_instructions",
    "sms_sender_id", "sms_templates",
)


async def get_clinic(clinic_id: int) -> Optional[dict]:
    """Return a clinic's config row, or None."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {_CLINIC_COLS} FROM clinics WHERE id = $1", clinic_id
        )
    return dict(row) if row else None


async def get_default_clinic_id() -> int:
    """Resolve the seeded 'default' clinic id (used when no channel maps)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id FROM clinics WHERE slug = 'default'")
        if row:
            return row["id"]
        # Fall back to the lowest id so a single-clinic install still works.
        row = await conn.fetchrow("SELECT id FROM clinics ORDER BY id LIMIT 1")
    if not row:
        raise RuntimeError("No clinic configured. Run `alembic upgrade head`.")
    return row["id"]


async def get_clinic_id_by_channel(kind: str, identifier: str) -> Optional[int]:
    """Map a channel identity (e.g. whatsapp number) to a clinic id."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT clinic_id FROM channels WHERE kind = $1 AND identifier = $2",
            kind, identifier,
        )
    return row["clinic_id"] if row else None


async def get_channel_by_kind_and_identifier(kind: str, identifier: str) -> Optional[dict]:
    """Return the full channel row for a given kind + identifier, or None."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {_CHANNEL_COLS} FROM channels WHERE kind = $1 AND identifier = $2",
            kind, identifier,
        )
    return dict(row) if row else None


async def get_channel_scope(kind: str, identifier: str) -> dict:
    """Return {"clinic_id": ..., "hospital_id": ..., "identifier": ...} for a channel.

    Exactly one of clinic_id / hospital_id will be set (per DB constraint).
    Returns all-None dict if no channel matches.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT clinic_id, hospital_id, identifier "
            "FROM channels WHERE kind = $1 AND identifier = $2",
            kind, identifier,
        )
    if not row:
        return {"clinic_id": None, "hospital_id": None, "identifier": identifier}
    return {
        "clinic_id": row["clinic_id"],
        "hospital_id": row["hospital_id"],
        "identifier": row["identifier"],
    }


async def list_clinics() -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(f"SELECT {_CLINIC_COLS} FROM clinics ORDER BY id")
    return [dict(r) for r in rows]


async def create_clinic(
    *, slug: str, name: str, doctor_name: str = "Doctor",
    doctor_phone: str = "", availability_days_ahead: int = 7,
    hospital_id: Optional[int] = None,
) -> Optional[dict]:
    """Create a clinic (and its default web channel). None if slug exists."""
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "INSERT INTO clinics (slug, name, doctor_name, doctor_phone, "
                    "availability_days_ahead, hospital_id) VALUES ($1,$2,$3,$4,$5,$6) "
                    f"RETURNING {_CLINIC_COLS}",
                    slug, name, doctor_name, doctor_phone, availability_days_ahead, hospital_id,
                )
                await conn.execute(
                    "INSERT INTO channels (clinic_id, kind, identifier) "
                    "VALUES ($1, 'web', $2) ON CONFLICT (kind, identifier) DO NOTHING",
                    row["id"], slug,
                )
        return dict(row)
    except asyncpg.UniqueViolationError:
        return None


async def update_clinic(clinic_id: int, **fields) -> Optional[dict]:
    """Patch editable settings on a clinic. Unknown/None fields are ignored.

    Returns the updated row, or None if the clinic does not exist / nothing to do.
    """
    updates = {k: v for k, v in fields.items() if k in _CLINIC_EDITABLE and v is not None}
    if not updates:
        return await get_clinic(clinic_id)

    cols = list(updates.keys())
    set_parts: list[str] = []
    values: list = []
    for i, c in enumerate(cols):
        val = updates[c]
        if c == "sms_templates":
            # JSONB column — serialize the dict and cast text -> jsonb.
            set_parts.append(f"{c} = ${i + 2}::jsonb")
            values.append(json.dumps(val))
        else:
            set_parts.append(f"{c} = ${i + 2}")
            values.append(val)
    set_clause = ", ".join(set_parts)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"UPDATE clinics SET {set_clause} WHERE id = $1 RETURNING {_CLINIC_COLS}",
            clinic_id, *values,
        )
    return dict(row) if row else None


# --------------------------------------------------------------------------- #
# Doctors (per-clinic roster; the primary mirrors into clinics.doctor_name/phone)
# --------------------------------------------------------------------------- #

_DOCTOR_COLS = (
    "id, clinic_id, name, specialty, degrees, description, phone, is_primary, "
    "created_at, (photo IS NOT NULL) AS has_photo"
)
_DOCTOR_EDITABLE = ("name", "specialty", "degrees", "description", "phone", "is_primary")


async def _resync_primary(conn, clinic_id: int) -> None:
    """Guarantee exactly one primary doctor and mirror it into the clinics row
    (which the agent + notification SMS read). Promotes the lowest-id doctor if
    none is flagged primary."""
    prim = await conn.fetchrow(
        "SELECT id, name, phone FROM doctors WHERE clinic_id=$1 AND is_primary "
        "ORDER BY id LIMIT 1",
        clinic_id,
    )
    if prim is None:
        prim = await conn.fetchrow(
            "SELECT id, name, phone FROM doctors WHERE clinic_id=$1 ORDER BY id LIMIT 1",
            clinic_id,
        )
        if prim is not None:
            await conn.execute(
                "UPDATE doctors SET is_primary=true WHERE id=$1", prim["id"]
            )
    if prim is not None:
        await conn.execute(
            "UPDATE clinics SET doctor_name=$1, doctor_phone=$2 WHERE id=$3",
            prim["name"], prim["phone"] or "", clinic_id,
        )


async def list_doctors(clinic_id: int) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT {_DOCTOR_COLS} FROM doctors WHERE clinic_id=$1 "
            "ORDER BY is_primary DESC, id",
            clinic_id,
        )
    return [dict(r) for r in rows]


async def add_doctor(
    *, clinic_id: int, name: str, specialty: str = "", phone: str = "",
    is_primary: bool = False, degrees: str = "", description: str = "",
) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            count = await conn.fetchval(
                "SELECT count(*) FROM doctors WHERE clinic_id=$1", clinic_id
            )
            make_primary = is_primary or count == 0
            if make_primary:
                await conn.execute(
                    "UPDATE doctors SET is_primary=false WHERE clinic_id=$1", clinic_id
                )
            row = await conn.fetchrow(
                "INSERT INTO doctors "
                "(clinic_id, name, specialty, degrees, description, phone, is_primary) "
                f"VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING {_DOCTOR_COLS}",
                clinic_id, name, specialty, degrees, description, phone, make_primary,
            )
            await _resync_primary(conn, clinic_id)
    return dict(row)


async def update_doctor(clinic_id: int, doctor_id: int, **fields) -> Optional[dict]:
    updates = {k: v for k, v in fields.items() if k in _DOCTOR_EDITABLE and v is not None}
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            if updates.get("is_primary") is True:
                await conn.execute(
                    "UPDATE doctors SET is_primary=false WHERE clinic_id=$1", clinic_id
                )
            if updates:
                cols = list(updates.keys())
                set_clause = ", ".join(f"{c}=${i + 3}" for i, c in enumerate(cols))
                row = await conn.fetchrow(
                    f"UPDATE doctors SET {set_clause} WHERE id=$1 AND clinic_id=$2 "
                    f"RETURNING {_DOCTOR_COLS}",
                    doctor_id, clinic_id, *[updates[c] for c in cols],
                )
            else:
                row = await conn.fetchrow(
                    f"SELECT {_DOCTOR_COLS} FROM doctors WHERE id=$1 AND clinic_id=$2",
                    doctor_id, clinic_id,
                )
            if row is None:
                return None
            await _resync_primary(conn, clinic_id)
    return dict(row)


async def delete_doctor(clinic_id: int, doctor_id: int) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            result = await conn.execute(
                "DELETE FROM doctors WHERE id=$1 AND clinic_id=$2", doctor_id, clinic_id
            )
            await _resync_primary(conn, clinic_id)
    try:
        return int(result.split()[-1]) > 0
    except (ValueError, IndexError):
        return False


async def set_doctor_photo(
    clinic_id: int, doctor_id: int, data: Optional[bytes], mime: Optional[str]
) -> bool:
    """Store (or clear, when data is None) a doctor's profile photo.

    Clinic-scoped like the other doctor mutations. Returns False when the
    doctor doesn't exist in this clinic.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE doctors SET photo=$3, photo_mime=$4 WHERE id=$1 AND clinic_id=$2",
            doctor_id, clinic_id, data, mime,
        )
    try:
        return int(result.split()[-1]) > 0
    except (ValueError, IndexError):
        return False


async def get_doctor_photo(doctor_id: int) -> Optional[tuple[bytes, str]]:
    """Photo bytes + mime for serving, or None when the doctor has no photo."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT photo, photo_mime FROM doctors WHERE id=$1", doctor_id
        )
    if row is None or row["photo"] is None:
        return None
    return bytes(row["photo"]), row["photo_mime"] or "image/jpeg"


# --------------------------------------------------------------------------- #
# Channels (per-clinic WhatsApp / SMS / voice number mappings)
# --------------------------------------------------------------------------- #


async def list_channels(clinic_id: int) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT {_CHANNEL_COLS} FROM channels WHERE clinic_id = $1 ORDER BY id",
            clinic_id,
        )
    return [dict(r) for r in rows]


async def add_channel(
    *,
    clinic_id: Optional[int] = None,
    hospital_id: Optional[int] = None,
    kind: str,
    identifier: str,
    label: str | None = None,
) -> Optional[dict]:
    """Map a channel identity (number/key) to a clinic or hospital. None if already taken.

    For voice_ivr channels, pass hospital_id and leave clinic_id as None.
    For all other kinds, pass clinic_id.
    """
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO channels (clinic_id, hospital_id, kind, identifier, label) "
                f"VALUES ($1,$2,$3,$4,$5) RETURNING {_CHANNEL_COLS}",
                clinic_id, hospital_id, kind, identifier, label,
            )
        return dict(row)
    except asyncpg.UniqueViolationError:
        return None


async def delete_channel(clinic_id: int, channel_id: int) -> bool:
    """Remove a channel mapping owned by this clinic. True if a row was deleted."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM channels WHERE id = $1 AND clinic_id = $2",
            channel_id, clinic_id,
        )
    # asyncpg returns e.g. "DELETE 1"; treat any positive count as success.
    try:
        return int(result.split()[-1]) > 0
    except (ValueError, IndexError):
        return False


# --------------------------------------------------------------------------- #
# Conversation log (append-only transcript per session/channel)
# --------------------------------------------------------------------------- #

async def log_turn(
    *, clinic_id: int, session_id: str, channel: str, role: str, text: str,
    channel_identifier: Optional[str] = None,
) -> None:
    """Best-effort append of one turn. Never raises into the caller."""
    if not clinic_id or not session_id or not text:
        return
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            try:
                await conn.execute(
                    "INSERT INTO conversation_log (clinic_id, session_id, channel, role, text, channel_identifier) "
                    "VALUES ($1,$2,$3,$4,$5,$6)",
                    clinic_id, session_id, channel or "text", role, text, channel_identifier,
                )
            except asyncpg.UndefinedColumnError:
                # channel_identifier column (0010) not yet migrated.
                await conn.execute(
                    "INSERT INTO conversation_log (clinic_id, session_id, channel, role, text) "
                    "VALUES ($1,$2,$3,$4,$5)",
                    clinic_id, session_id, channel or "text", role, text,
                )
    except Exception:  # logging must never break a conversation
        log.warning("conversation_log insert failed", exc_info=True)


async def delete_conversation(clinic_id: int, session_id: str) -> int:
    """Delete all logged turns for one session (clinic-scoped). Returns rows removed."""
    if not clinic_id or not session_id:
        return 0
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM conversation_log WHERE clinic_id = $1 AND session_id = $2",
            clinic_id, session_id,
        )
    # asyncpg returns a tag like "DELETE 5".
    try:
        return int(result.split()[-1])
    except (ValueError, IndexError, AttributeError):
        return 0


# --------------------------------------------------------------------------- #
# SMS log (append-only record of outbound messages, for console tracking)      #
# --------------------------------------------------------------------------- #

async def log_sms(
    *, clinic_id: Optional[int], to_number: str, body: str, kind: str,
    status: str, provider: Optional[str], error: Optional[str] = None,
) -> None:
    """Best-effort record of one outbound SMS. Recipient + body are encrypted
    (Fernet) like other patient PII. Never raises into the caller."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO sms_log (clinic_id, to_number, body, kind, status, provider, error) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7)",
                clinic_id, encrypt_field(to_number), encrypt_field(body),
                kind, status, provider, error,
            )
    except Exception:  # tracking must never break an SMS send
        log.warning("sms_log insert failed", exc_info=True)


async def get_sms(sms_id: int, clinic_id: int) -> Optional[dict]:
    """Fetch one outbound SMS by id (clinic-scoped), with PII decrypted."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        r = await conn.fetchrow(
            "SELECT id, clinic_id, to_number, body, kind, status, provider, error, created_at "
            "FROM sms_log WHERE id = $1 AND clinic_id = $2",
            sms_id, clinic_id,
        )
    if not r:
        return None
    return {**dict(r),
            "to_number": decrypt_field(r["to_number"]),
            "body": decrypt_field(r["body"])}


async def list_sms(
    clinic_id: int, *, limit: int = 200, kind: Optional[str] = None,
    status: Optional[str] = None,
) -> list[dict]:
    """Outbound SMS for a clinic, newest first, with PII decrypted."""
    clauses = ["clinic_id = $1"]
    args: list = [clinic_id]
    if kind:
        args.append(kind)
        clauses.append(f"kind = ${len(args)}")
    if status:
        args.append(status)
        clauses.append(f"status = ${len(args)}")
    args.append(limit)
    where = " AND ".join(clauses)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, clinic_id, to_number, body, kind, status, provider, error, created_at "
            f"FROM sms_log WHERE {where} ORDER BY created_at DESC LIMIT ${len(args)}",
            *args,
        )
    return [
        {**dict(r),
         "to_number": decrypt_field(r["to_number"]),
         "body": decrypt_field(r["body"])}
        for r in rows
    ]


async def list_conversations(
    clinic_id: int, limit: int = 50, offset: int = 0
) -> list[dict]:
    """Recent conversations for a clinic: one row per session with a summary."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT session_id,
                   max(channel)            AS channel,
                   count(*)                AS turns,
                   max(created_at)         AS last_at,
                   min(created_at)         AS started_at,
                   (array_agg(text ORDER BY created_at DESC))[1] AS last_text
            FROM conversation_log
            WHERE clinic_id = $1
            GROUP BY session_id
            ORDER BY last_at DESC
            LIMIT $2 OFFSET $3
            """,
            clinic_id, limit, offset,
        )
    return [dict(r) for r in rows]


async def get_conversation(clinic_id: int, session_id: str) -> list[dict]:
    """Full ordered transcript for one session, scoped to the clinic."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT role, text, channel, created_at FROM conversation_log "
            "WHERE clinic_id = $1 AND session_id = $2 ORDER BY created_at, id",
            clinic_id, session_id,
        )
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# Hospitals (multi-department tenants)                                        #
# --------------------------------------------------------------------------- #

_HOSPITAL_COLS = "id, slug, name, address, license_number, timezone, status, created_at"

# Fields a hospital_admin / platform_admin may edit on a hospital (not the slug).
_HOSPITAL_EDITABLE = ("name", "address", "license_number", "timezone", "status")


async def get_hospital(hospital_id: int) -> Optional[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {_HOSPITAL_COLS} FROM hospitals WHERE id = $1", hospital_id
        )
    return dict(row) if row else None


async def list_hospitals() -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT {_HOSPITAL_COLS} FROM hospitals ORDER BY id"
        )
    return [dict(r) for r in rows]


async def create_hospital(
    *, slug: str, name: str, address: str = "", license_number: str = "",
    timezone: str = "Asia/Dhaka",
) -> Optional[dict]:
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO hospitals (slug, name, address, license_number, timezone) "
                f"VALUES ($1,$2,$3,$4,$5) RETURNING {_HOSPITAL_COLS}",
                slug, name, address, license_number, timezone,
            )
        return dict(row)
    except asyncpg.UniqueViolationError:
        return None


async def update_hospital(hospital_id: int, **fields) -> Optional[dict]:
    """Patch editable hospital details. Unknown/None fields are ignored.

    Returns the updated row, or None if the hospital does not exist.
    """
    updates = {k: v for k, v in fields.items() if k in _HOSPITAL_EDITABLE and v is not None}
    if not updates:
        return await get_hospital(hospital_id)

    cols = list(updates.keys())
    set_clause = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(cols))
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"UPDATE hospitals SET {set_clause} WHERE id = $1 RETURNING {_HOSPITAL_COLS}",
            hospital_id, *[updates[c] for c in cols],
        )
    return dict(row) if row else None


async def get_hospital_id_for_clinic(clinic_id: int) -> Optional[int]:
    """Return the hospital_id a department (clinic) belongs to, or None."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT hospital_id FROM clinics WHERE id = $1", clinic_id
        )


async def list_departments(hospital_id: int) -> list[dict]:
    """Return all clinic rows linked to this hospital (= departments)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, slug, name, doctor_name, specialty_code, floor, phone_ext "
            "FROM clinics WHERE hospital_id = $1 ORDER BY id",
            hospital_id,
        )
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# Patients (MRN-registered hospital patient identity)                         #
# --------------------------------------------------------------------------- #

_PATIENT_COLS = "id, hospital_id, mrn, name, phone, age, gender, account_id, created_at"


async def get_patient_by_phone(hospital_id: int, phone: str) -> Optional[dict]:
    """Look up a registered patient by phone number within a hospital."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {_PATIENT_COLS} FROM patients "
            "WHERE hospital_id = $1 AND phone = $2 LIMIT 1",
            hospital_id, phone,
        )
    return dict(row) if row else None


async def get_patient_by_mrn(hospital_id: int, mrn: str) -> Optional[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {_PATIENT_COLS} FROM patients WHERE hospital_id=$1 AND mrn=$2",
            hospital_id, mrn,
        )
    return dict(row) if row else None


async def create_patient(
    *, hospital_id: int, name: str, phone: str, age: Optional[int] = None,
    gender: Optional[str] = None, account_id: Optional[int] = None,
) -> dict:
    """Register a new patient and assign an auto-generated MRN.

    Advisory lock on hospital_id serialises concurrent registrations so two
    simultaneous calls never read the same count and produce duplicate MRNs.

    account_id (migration 0015) links the per-hospital record to a platform-wide
    patient account; silently ignored if the column does not yet exist.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # pg_advisory_xact_lock serialises MRN assignment per hospital.
            await conn.execute("SELECT pg_advisory_xact_lock($1)", hospital_id)
            count = await conn.fetchval(
                "SELECT count(*) FROM patients WHERE hospital_id = $1", hospital_id
            )
            mrn = f"MRN-{hospital_id}-{count + 1:06d}"
            try:
                row = await conn.fetchrow(
                    "INSERT INTO patients (hospital_id, mrn, name, phone, age, gender, account_id) "
                    f"VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING {_PATIENT_COLS}",
                    hospital_id, mrn, name, phone, age, gender, account_id,
                )
            except asyncpg.UndefinedColumnError:
                # account_id column (0015) not yet migrated.
                row = await conn.fetchrow(
                    "INSERT INTO patients (hospital_id, mrn, name, phone, age, gender) "
                    f"VALUES ($1,$2,$3,$4,$5,$6) RETURNING {_PATIENT_COLS}",
                    hospital_id, mrn, name, phone, age, gender,
                )
    return dict(row)


async def get_or_create_patient(
    *, hospital_id: int, name: str, phone: str, age: Optional[int] = None,
    account_id: Optional[int] = None,
) -> dict:
    """Look up a patient by phone; register as new if not found.

    When account_id is given and an existing record is not yet linked to any
    account, back-fill the link so future bookings resolve to one identity.
    """
    existing = await get_patient_by_phone(hospital_id, phone)
    if existing:
        if account_id and not existing.get("account_id"):
            await link_patient_to_account(existing["id"], account_id)
            existing["account_id"] = account_id
        return existing
    return await create_patient(
        hospital_id=hospital_id, name=name, phone=phone, age=age, account_id=account_id
    )


async def link_patient_to_account(patient_id: int, account_id: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE patients SET account_id = $1 WHERE id = $2", account_id, patient_id
        )


async def list_patients(hospital_id: int, q: Optional[str] = None, limit: int = 100) -> list[dict]:
    pool = await get_pool()
    params: list = [hospital_id]
    where = "WHERE hospital_id = $1"
    if q:
        idx = len(params) + 1
        params.append(f"%{q}%")
        where += f" AND (name ILIKE ${idx} OR phone ILIKE ${idx} OR mrn ILIKE ${idx})"
    params.append(int(limit))
    lim_n = len(params)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT {_PATIENT_COLS} FROM patients {where} ORDER BY id DESC LIMIT ${lim_n}",
            *params,
        )
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# Queue / token system                                                         #
# --------------------------------------------------------------------------- #

async def create_token(
    *, appointment_id: str, hospital_id: int, department_id: int,
    token_date: str, doctor_id: Optional[int] = None, token_prefix: str = "A",
) -> dict:
    """Atomically assign the next sequential token number for dept+date."""
    import datetime as _dt
    date_obj = _dt.date.fromisoformat(token_date)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            max_num = await conn.fetchval(
                "SELECT COALESCE(MAX(token_number), 0) FROM appointment_tokens "
                "WHERE department_id = $1 AND token_date = $2 FOR UPDATE",
                department_id, date_obj,
            )
            token_number = max_num + 1
            row = await conn.fetchrow(
                "INSERT INTO appointment_tokens "
                "(appointment_id, hospital_id, department_id, doctor_id, "
                " token_date, token_number, token_prefix) "
                "VALUES ($1::uuid, $2, $3, $4, $5, $6, $7) "
                "RETURNING id, appointment_id::text, hospital_id, department_id, "
                "doctor_id, token_date, token_number, token_prefix, status, created_at",
                appointment_id, hospital_id, department_id, doctor_id,
                date_obj, token_number, token_prefix,
            )
    return dict(row)


async def list_tokens_today(
    department_id: int, hospital_id: Optional[int] = None, clinic_id: Optional[int] = None,
) -> list[dict]:
    """Current queue for a department — today's tokens ordered by number.

    Tenant scoping: hospital-tenant staff pass hospital_id (their hospital may
    have several departments); standalone-clinic staff have no hospital_id, so
    pass clinic_id instead — department_id must equal their own clinic_id, or
    the query matches nothing rather than leaking another tenant's queue.
    """
    import datetime as _dt
    today = _dt.date.today()
    pool = await get_pool()
    params: list = [department_id, today]
    where = "t.department_id = $1 AND t.token_date = $2"
    if hospital_id is not None:
        params.append(hospital_id)
        where += f" AND t.hospital_id = ${len(params)}"
    elif clinic_id is not None:
        params.append(clinic_id)
        where += f" AND t.department_id = ${len(params)}"
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT t.id, t.appointment_id::text, t.token_number, t.token_prefix, "
            "t.status, t.called_at, t.completed_at, "
            "a.patient_name, a.patient_mobile "
            "FROM appointment_tokens t "
            "JOIN appointments a ON a.id = t.appointment_id "
            f"WHERE {where} "
            "ORDER BY t.token_number",
            *params,
        )
    return [
        {**dict(r),
         "patient_name": decrypt_field(r["patient_name"]),
         "patient_mobile": decrypt_field(r["patient_mobile"])}
        for r in rows
    ]


async def call_token(
    token_id: int, hospital_id: Optional[int] = None, clinic_id: Optional[int] = None,
) -> Optional[dict]:
    """Mark a token as 'called' and return its row (for SMS notification).

    Tenant scoping: hospital-tenant staff pass hospital_id; standalone-clinic
    staff (no hospital_id) pass clinic_id instead, scoped via department_id —
    without one of these, cross-tenant manipulation of tokens by guessing an
    integer token_id was possible.
    """
    pool = await get_pool()
    params: list = [token_id]
    where = "id=$1 AND status='waiting'"
    if hospital_id is not None:
        params.append(hospital_id)
        where += f" AND hospital_id=${len(params)}"
    elif clinic_id is not None:
        params.append(clinic_id)
        where += f" AND department_id=${len(params)}"
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"UPDATE appointment_tokens SET status='called', called_at=now() "
            f"WHERE {where} "
            "RETURNING id, appointment_id::text, token_number, token_prefix, department_id",
            *params,
        )
    return dict(row) if row else None


async def complete_token(
    token_id: int, hospital_id: Optional[int] = None, clinic_id: Optional[int] = None,
) -> bool:
    """Mark a token as completed.

    Tenant scoping: hospital_id for hospital-tenant staff, else clinic_id
    (scoped via department_id) for standalone-clinic staff — see call_token.
    """
    pool = await get_pool()
    params: list = [token_id]
    where = "id=$1 AND status IN ('called','in_progress')"
    if hospital_id is not None:
        params.append(hospital_id)
        where += f" AND hospital_id=${len(params)}"
    elif clinic_id is not None:
        params.append(clinic_id)
        where += f" AND department_id=${len(params)}"
    async with pool.acquire() as conn:
        result = await conn.execute(
            f"UPDATE appointment_tokens SET status='completed', completed_at=now() "
            f"WHERE {where}",
            *params,
        )
    try:
        return int(result.split()[-1]) > 0
    except (ValueError, IndexError):
        return False


async def get_user_by_email(email: str) -> Optional[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, clinic_id, email, password_hash, role, hospital_id FROM users WHERE email = $1",
            email,
        )
    return dict(row) if row else None


async def create_user(*, email: str, password_hash: str,
                      role: str = "hospital_admin",
                      clinic_id: Optional[int] = None,
                      hospital_id: Optional[int] = None) -> Optional[dict]:
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO users (clinic_id, hospital_id, email, password_hash, role) "
                "VALUES ($1,$2,$3,$4,$5) RETURNING id, clinic_id, hospital_id, email, role",
                clinic_id, hospital_id, email, password_hash, role,
            )
        return dict(row)
    except asyncpg.UniqueViolationError:
        return None


# --------------------------------------------------------------------------- #
# Patient accounts (platform-wide patient self-service login)                 #
# --------------------------------------------------------------------------- #

_PATIENT_ACCOUNT_COLS = "id, email, phone, name, created_at"


async def create_patient_account(
    *, email: str, password_hash: str, name: str = "", phone: str = "",
) -> Optional[dict]:
    """Create a patient login account. Returns None if the email is taken."""
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO patient_accounts (email, password_hash, name, phone) "
                f"VALUES ($1,$2,$3,$4) RETURNING {_PATIENT_ACCOUNT_COLS}",
                email, password_hash, name, phone,
            )
        return dict(row)
    except asyncpg.UniqueViolationError:
        return None


async def get_patient_account_by_email(email: str) -> Optional[dict]:
    """Return the full account row (including password_hash) for login."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, email, phone, name, password_hash, created_at "
            "FROM patient_accounts WHERE email = $1",
            email,
        )
    return dict(row) if row else None


async def get_patient_account(account_id: int) -> Optional[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {_PATIENT_ACCOUNT_COLS} FROM patient_accounts WHERE id = $1",
            account_id,
        )
    return dict(row) if row else None


async def get_patient_account_by_phone(phone: str) -> Optional[dict]:
    """Return an account by registered phone (for SMS password reset)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, email, phone, name, password_hash, created_at "
            "FROM patient_accounts WHERE phone = $1 ORDER BY id LIMIT 1",
            phone,
        )
    return dict(row) if row else None


async def update_patient_password(account_id: int, password_hash: str) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE patient_accounts SET password_hash = $1 WHERE id = $2",
            password_hash, account_id,
        )
    try:
        return int(result.split()[-1]) > 0
    except (ValueError, IndexError):
        return False


# --- Password reset OTP (SMS-delivered, single-use, short-lived) ---

async def create_password_reset(account_id: int, code_hash: str, expires_at) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Invalidate any prior unused codes for this account, then insert the new one.
        await conn.execute(
            "UPDATE password_resets SET used = TRUE WHERE account_id = $1 AND used = FALSE",
            account_id,
        )
        await conn.execute(
            "INSERT INTO password_resets (account_id, code_hash, expires_at) VALUES ($1, $2, $3)",
            account_id, code_hash, expires_at,
        )


async def get_active_password_reset(account_id: int) -> Optional[dict]:
    """Return the latest unused, unexpired reset row for the account, or None."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, code_hash FROM password_resets "
            "WHERE account_id = $1 AND used = FALSE AND expires_at > now() "
            "ORDER BY id DESC LIMIT 1",
            account_id,
        )
    return dict(row) if row else None


async def mark_password_reset_used(reset_id: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE password_resets SET used = TRUE WHERE id = $1", reset_id)


async def list_appointments_for_account(account_id: int) -> list[dict]:
    """Every appointment booked under any of this account's hospital patient
    records, newest first, enriched with hospital/department/doctor labels."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT a.id::text AS id, a.patient_name, a.patient_mobile,
                   a.scheduled_at, a.duration_mins, a.status, a.serial_number,
                   a.created_at,
                   c.id   AS clinic_id,   c.name AS department_name,
                   h.id   AS hospital_id, h.name AS hospital_name,
                   d.name AS doctor_name
            FROM appointments a
            JOIN patients p   ON a.patient_id = p.id
            LEFT JOIN clinics c   ON a.clinic_id = c.id
            LEFT JOIN hospitals h ON c.hospital_id = h.id
            LEFT JOIN doctors d   ON a.doctor_id = d.id
            WHERE p.account_id = $1
            ORDER BY a.scheduled_at DESC
            """,
            account_id,
        )
    return [
        {**dict(r),
         "patient_name": decrypt_field(r["patient_name"]),
         "patient_mobile": decrypt_field(r["patient_mobile"])}
        for r in rows
    ]


async def cancel_appointment_for_account(account_id: int, appointment_id: str) -> bool:
    """Cancel a confirmed appointment that belongs to one of this account's
    hospital patient records. True if a row changed (ownership enforced in SQL)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE appointments "
            "SET status = 'cancelled', cancelled_at = now(), updated_at = now() "
            "FROM patients p "
            "WHERE appointments.id = $1::uuid AND appointments.patient_id = p.id "
            "AND p.account_id = $2 AND appointments.status = 'confirmed' "
            "RETURNING appointments.clinic_id AS clinic_id",
            appointment_id, account_id,
        )
    if row is None:
        return False
    await _record_appointment_event(
        appointment_id=appointment_id, clinic_id=row["clinic_id"],
        event_type="cancelled", to_status="cancelled", actor_role="patient",
    )
    return True


# --------------------------------------------------------------------------- #
# Escalations (agent → human handoff queue)
# --------------------------------------------------------------------------- #

async def create_escalation(
    *, clinic_id: Optional[int], session_id: str, channel: str, reason: str
) -> Optional[int]:
    """Record a conversation flagged for staff follow-up. Returns the row id."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO escalations (clinic_id, session_id, channel, reason) "
            "VALUES ($1, $2, $3, $4) RETURNING id",
            clinic_id, session_id, channel or "text", reason or "",
        )


async def list_escalations(clinic_id: int, status: str = "open") -> list[dict]:
    """Escalations for this clinic, newest first."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, clinic_id, session_id, channel, reason, status, "
            "created_at, resolved_at FROM escalations "
            "WHERE clinic_id = $1 AND ($2 = '' OR status = $2) "
            "ORDER BY created_at DESC LIMIT 100",
            clinic_id, status or "",
        )
    return [dict(r) for r in rows]


async def resolve_escalation(clinic_id: int, escalation_id: int) -> bool:
    """Mark an escalation resolved (tenant-scoped). True if a row changed."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE escalations SET status = 'resolved', resolved_at = now() "
            "WHERE id = $1 AND clinic_id = $2 AND status = 'open'",
            escalation_id, clinic_id,
        )
    return result.endswith("1")


# --------------------------------------------------------------------------- #
# Two-way reminder replies (১ = confirm, ২ = cancel)
# --------------------------------------------------------------------------- #

async def get_reminded_appointment_by_phone(phone: str) -> Optional[dict]:
    """The upcoming confirmed appointment whose reminder went out and whose
    patient mobile matches this sender — but ONLY when it's unambiguous.

    Mobiles are stored field-encrypted (non-deterministic), so candidates are
    decrypted and compared in Python — the reminded-upcoming set is small.
    Matches on the last 10 digits so +880… and 0… formats agree. If the same
    phone has more than one reminded upcoming appointment, returns None
    (ambiguous — a plain "১"/"২" reply could apply to either) so the caller
    falls through to the LLM, which can ask the patient which one they mean,
    instead of silently confirming/cancelling the wrong appointment.
    """
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) < 10:
        return None
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id::text AS id, clinic_id, patient_name, patient_mobile, "
            "scheduled_at, serial_number, patient_confirmed_at "
            "FROM appointments "
            "WHERE status = 'confirmed' AND reminder_sent_at IS NOT NULL "
            "AND scheduled_at > now() ORDER BY scheduled_at ASC LIMIT 200"
        )
    matches = []
    for r in rows:
        mob = re.sub(r"\D", "", decrypt_field(r["patient_mobile"]) or "")
        if mob and mob[-10:] == digits[-10:]:
            matches.append({
                **dict(r),
                "patient_name": decrypt_field(r["patient_name"]),
                "patient_mobile": mob,
            })
    return matches[0] if len(matches) == 1 else None


async def confirm_appointment_by_patient(appointment_id: str) -> None:
    """Stamp a reminder reply of "১" — the patient confirmed they will attend."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE appointments SET patient_confirmed_at = now(), updated_at = now() "
            "WHERE id = $1::uuid AND status = 'confirmed'",
            appointment_id,
        )


async def cancel_appointment_by_patient(appointment_id: str) -> bool:
    """Cancel a confirmed appointment from a reminder reply of "২"."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE appointments "
            "SET status = 'cancelled', cancelled_at = now(), updated_at = now() "
            "WHERE id = $1::uuid AND status = 'confirmed' "
            "RETURNING clinic_id",
            appointment_id,
        )
    if row is None:
        return False
    await _record_appointment_event(
        appointment_id=appointment_id, clinic_id=row["clinic_id"],
        event_type="cancelled", to_status="cancelled", actor_role="patient",
        note="reminder reply",
    )
    return True


# --------------------------------------------------------------------------- #
# Channel stats (calls & appointments per voice number)
# --------------------------------------------------------------------------- #

async def get_channel_stats(clinic_id: int) -> list[dict]:
    """Return per-channel call and appointment counts for voice numbers."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
              c.id,
              c.identifier,
              c.label,
              COALESCE(s.calls_received, 0)    AS calls_received,
              COALESCE(s.appts_taken, 0)        AS appointments_taken
            FROM channels c
            LEFT JOIN (
              SELECT
                cl.channel_identifier,
                COUNT(DISTINCT cl.session_id)                AS calls_received,
                COUNT(DISTINCT a.id)                         AS appts_taken
              FROM conversation_log cl
              LEFT JOIN appointments a
                ON a.session_id = cl.session_id AND a.clinic_id = cl.clinic_id
              WHERE cl.clinic_id = $1
                AND cl.channel = 'voice'
                AND cl.channel_identifier IS NOT NULL
              GROUP BY cl.channel_identifier
            ) s ON s.channel_identifier = c.identifier
            WHERE c.clinic_id = $1
              AND c.kind IN ('voice', 'voice_sip')
            ORDER BY s.calls_received DESC, c.id
            """,
            clinic_id,
        )
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# Reporting / analytics aggregates (date-range, clinic-scoped)
# --------------------------------------------------------------------------- #

async def appointment_stats(clinic_id: int, date_from: str, date_to: str) -> dict:
    """Appointment counts by status, no-show/completion rates, per-doctor load,
    and a daily booking trend over [date_from, date_to] (inclusive), keyed on the
    scheduled date."""
    df = date.fromisoformat(date_from)
    dt_excl = date.fromisoformat(date_to) + timedelta(days=1)
    pool = await get_pool()
    async with pool.acquire() as conn:
        status_rows = await conn.fetch(
            "SELECT status, COUNT(*) AS n FROM appointments "
            "WHERE clinic_id = $1 AND scheduled_at >= $2 AND scheduled_at < $3 "
            "GROUP BY status",
            clinic_id, df, dt_excl,
        )
        doc_rows = await conn.fetch(
            "SELECT a.doctor_id, COALESCE(d.name, 'Unassigned') AS name, COUNT(*) AS n "
            "FROM appointments a LEFT JOIN doctors d ON d.id = a.doctor_id "
            "WHERE a.clinic_id = $1 AND a.scheduled_at >= $2 AND a.scheduled_at < $3 "
            "GROUP BY a.doctor_id, d.name ORDER BY n DESC",
            clinic_id, df, dt_excl,
        )
        trend_rows = await conn.fetch(
            "SELECT scheduled_at::date AS day, COUNT(*) AS n FROM appointments "
            "WHERE clinic_id = $1 AND scheduled_at >= $2 AND scheduled_at < $3 "
            "GROUP BY day ORDER BY day",
            clinic_id, df, dt_excl,
        )
    status_counts = {r["status"]: r["n"] for r in status_rows}
    total = sum(status_counts.values())
    completed = status_counts.get("completed", 0)
    no_show = status_counts.get("no_show", 0)
    cancelled = status_counts.get("cancelled", 0)
    # Rates are over appointments that reached an outcome (completed or no-show).
    finished = completed + no_show
    no_show_rate = round(no_show / finished * 100, 1) if finished else 0.0
    completion_rate = round(completed / finished * 100, 1) if finished else 0.0
    return {
        "total": total,
        "status_counts": status_counts,
        "completed": completed,
        "no_show": no_show,
        "cancelled": cancelled,
        "no_show_rate": no_show_rate,
        "completion_rate": completion_rate,
        "per_doctor": [
            {"doctor_id": r["doctor_id"], "name": r["name"], "count": r["n"]}
            for r in doc_rows
        ],
        "daily": [
            {"day": r["day"].isoformat(), "count": r["n"]} for r in trend_rows
        ],
    }


async def sms_stats(clinic_id: int, date_from: str, date_to: str) -> dict:
    """Outbound SMS counts by local status (sent/failed/skipped) and by kind,
    over [date_from, date_to] (inclusive)."""
    df = date.fromisoformat(date_from)
    dt_excl = date.fromisoformat(date_to) + timedelta(days=1)
    pool = await get_pool()
    async with pool.acquire() as conn:
        by_status = await conn.fetch(
            "SELECT status, COUNT(*) AS n FROM sms_log "
            "WHERE clinic_id = $1 AND created_at >= $2 AND created_at < $3 "
            "GROUP BY status",
            clinic_id, df, dt_excl,
        )
        by_kind = await conn.fetch(
            "SELECT kind, COUNT(*) AS n FROM sms_log "
            "WHERE clinic_id = $1 AND created_at >= $2 AND created_at < $3 "
            "GROUP BY kind ORDER BY n DESC",
            clinic_id, df, dt_excl,
        )
    return {
        "by_status": {r["status"]: r["n"] for r in by_status},
        "by_kind": [{"kind": r["kind"], "count": r["n"]} for r in by_kind],
    }


# ---------------------------------------------------------------------------
# Hospital document registry (tracks RAG ingestions)
# ---------------------------------------------------------------------------

_DOC_COLS = "id, hospital_id, filename, content_type, chunk_count, created_at"


async def create_hospital_document(
    *,
    hospital_id: int,
    filename: str,
    content_type: str,
    chunk_count: int,
) -> dict:
    async with (await get_pool()).acquire() as conn:
        row = await conn.fetchrow(
            f"INSERT INTO hospital_documents (hospital_id, filename, content_type, chunk_count) "
            f"VALUES ($1, $2, $3, $4) RETURNING {_DOC_COLS}",
            hospital_id, filename, content_type, chunk_count,
        )
    return dict(row)


async def list_hospital_documents(hospital_id: int) -> list[dict]:
    async with (await get_pool()).acquire() as conn:
        rows = await conn.fetch(
            f"SELECT {_DOC_COLS} FROM hospital_documents "
            "WHERE hospital_id = $1 ORDER BY created_at DESC",
            hospital_id,
        )
    return [dict(r) for r in rows]


async def get_hospital_document(doc_id: int) -> Optional[dict]:
    async with (await get_pool()).acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {_DOC_COLS} FROM hospital_documents WHERE id = $1",
            doc_id,
        )
    return dict(row) if row else None


async def delete_hospital_document(doc_id: int) -> bool:
    async with (await get_pool()).acquire() as conn:
        result = await conn.execute(
            "DELETE FROM hospital_documents WHERE id = $1", doc_id
        )
    return result == "DELETE 1"


async def hospital_has_documents(hospital_id: int) -> bool:
    async with (await get_pool()).acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM hospital_documents WHERE hospital_id = $1",
            hospital_id,
        )
    return (count or 0) > 0
