"""Postgres data access for the appointment agent.

Holds a shared asyncpg pool and the two operations the graph needs:
  - get_available_slots(): free slots = schedule minus confirmed bookings
  - book_appointment(): insert a confirmed appointment row
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, time, timedelta, timezone
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
            "fee_new, fee_followup, (photo IS NOT NULL) AS has_photo "
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
                "fee_new, fee_followup, (photo IS NOT NULL) AS has_photo FROM doctors "
                "WHERE id = $1 AND clinic_id = $2",
                doctor_id, clinic_id,
            )
        else:
            row = await conn.fetchrow(
                "SELECT id, clinic_id, name, specialty, degrees, description, phone, "
                "is_primary, fee_new, fee_followup, "
                "(photo IS NOT NULL) AS has_photo FROM doctors "
                "WHERE id = $1",
                doctor_id,
            )
    return dict(row) if row else None


# A slot is "taken" (unavailable to a new booking) when it's confirmed, OR
# still held by an UNEXPIRED pending payment — a stale pending hold whose TTL
# passed must not block the slot forever (the sweep loop cancels it soon,
# but a patient tapping the slot before that sweep runs should still see it
# as open). Matches uq_confirmed_slot's partial-index predicate (0026).
_OPEN_SLOT_STATUS_SQL = (
    "status = 'confirmed' OR (status = 'pending_payment' AND payment_expires_at > now())"
)


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
                    "WHERE clinic_id = $1 AND doctor_id = $2 AND "
                    f"({_OPEN_SLOT_STATUS_SQL}) "
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
                f"WHERE clinic_id = $1 AND ({_OPEN_SLOT_STATUS_SQL}) "
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
    actor_role: Optional[str] = None,
    status: str = "confirmed",
    payment_ttl_minutes: Optional[int] = None,
    _retry: bool = True,
) -> Optional[dict]:
    """Insert an appointment. Patient name and mobile are encrypted when
    PATIENT_ENCRYPTION_KEY is set. Returns the new UUID, or None on race-loss.

    Optional hospital-mode fields (patient_id, doctor_id, appointment_type,
    consent_at) require migration 0007 columns; they are silently ignored if
    those columns do not yet exist.

    session_id (migration 0010) links the appointment back to the conversation
    that booked it; silently ignored if the column does not yet exist.

    actor_role overrides who the audit event credits ("patient" for direct
    portal bookings); default keeps the session-id heuristic (agent vs blank).

    status="pending_payment" (migration 0026) HOLDS the slot for
    payment_ttl_minutes instead of confirming immediately — a booking fee
    applies; confirm_paid_booking() promotes it to "confirmed" once payment
    succeeds, or the expiry sweep cancels it and frees the slot. On a
    pre-0026 schema this silently falls back to the table's 'confirmed'
    default (the whole payments feature requires 0026 to be meaningful).
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
    ttl = payment_ttl_minutes if status == "pending_payment" else None
    try:
        async with pool.acquire() as conn:
            try:
                row = await conn.fetchrow(
                    "INSERT INTO appointments "
                    "(clinic_id, patient_name, patient_age, patient_mobile, "
                    " scheduled_at, duration_mins, patient_id, doctor_id, "
                    " appointment_type, consent_at, session_id, status, payment_expires_at) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, "
                    " now() + make_interval(mins => $13)) RETURNING id",
                    clinic_id,
                    encrypt_field(patient_name),
                    patient_age,
                    encrypt_field(patient_mobile),
                    dt, duration_mins,
                    patient_id, doctor_id, appointment_type, consent_dt,
                    session_id, status, ttl,
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
            event_type="created", to_status=status, to_time=dt,
            actor_role=actor_role if actor_role is not None
            else ("agent" if session_id else ""),
        )
        return {"id": new_id, "serial_number": serial_number}
    except asyncpg.UniqueViolationError:
        # The slot looked open when the caller checked, but lost the race —
        # OR it was actually a stale pending_payment hold whose TTL already
        # passed (the 60s sweep hasn't gotten to it yet). Clear any expired
        # hold on this exact slot and retry ONCE before reporting a genuine
        # race-loss; uq_confirmed_slot only allows one row per (clinic,time)
        # among confirmed/pending_payment, so this is safe to attempt blind.
        if not _retry:
            return None
        try:
            async with pool.acquire() as conn:
                # Scope the clear to the exact (clinic, doctor, time) slot that
                # collided — uq_confirmed_slot is now per-doctor, so clearing by
                # (clinic, time) alone could cancel a *different* doctor's live
                # hold that never conflicted with this insert.
                cleared = await conn.execute(
                    "UPDATE appointments SET status = 'cancelled', cancelled_at = now() "
                    "WHERE clinic_id = $1 AND scheduled_at = $2 AND status = 'pending_payment' "
                    "AND doctor_id IS NOT DISTINCT FROM $3 "
                    "AND payment_expires_at < now()",
                    clinic_id, dt, doctor_id,
                )
        except Exception:
            cleared = "UPDATE 0"
        if not cleared.endswith("0"):
            return await book_appointment(
                clinic_id=clinic_id, patient_name=patient_name, patient_age=patient_age,
                patient_mobile=patient_mobile, scheduled_at=scheduled_at,
                duration_mins=duration_mins, patient_id=patient_id, doctor_id=doctor_id,
                appointment_type=appointment_type, consent_at=consent_at,
                session_id=session_id, actor_role=actor_role, status=status,
                payment_ttl_minutes=payment_ttl_minutes, _retry=False,
            )
        return None


# --------------------------------------------------------------------------- #
# Payments — booking fees + patient subscriptions
# --------------------------------------------------------------------------- #

async def create_payment(
    *, kind: str, amount: int, provider: str, provider_ref: str,
    appointment_id: Optional[str] = None, account_id: Optional[int] = None,
    hospital_id: Optional[int] = None, currency: str = "BDT",
) -> dict:
    """Record a new payment attempt (status='initiated'). Returns the row."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO payments (kind, appointment_id, account_id, hospital_id, "
            "amount, currency, provider, provider_ref) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8) "
            "RETURNING id::text AS id, kind, appointment_id::text AS appointment_id, "
            "account_id, hospital_id, amount, currency, provider, provider_ref, status",
            kind, appointment_id, account_id, hospital_id, amount, currency, provider, provider_ref,
        )
    return dict(row)


async def get_payment(payment_id: str) -> Optional[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id::text AS id, kind, appointment_id::text AS appointment_id, "
            "account_id, hospital_id, amount, currency, provider, provider_ref, "
            "val_id, status, created_at, paid_at FROM payments WHERE id = $1::uuid",
            payment_id,
        )
    return dict(row) if row else None


async def get_payment_by_provider_ref(provider: str, provider_ref: str) -> Optional[dict]:
    """Look up a payment by its gateway-side transaction id (IPN lookup key)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id::text AS id, kind, appointment_id::text AS appointment_id, "
            "account_id, hospital_id, amount, currency, provider, provider_ref, "
            "val_id, status, created_at, paid_at FROM payments "
            "WHERE provider = $1 AND provider_ref = $2",
            provider, provider_ref,
        )
    return dict(row) if row else None


async def confirm_paid_booking(payment_id: str, *, val_id: str, raw: dict) -> dict:
    """Mark a payment paid and promote its linked appointment (if any) to confirmed.

    Idempotent: a gateway retries the IPN on anything but a clean 200, so a
    re-delivery of an already-paid payment must be a safe no-op, not a
    double-confirm or an error. The UPDATE only matches rows still in
    'initiated'/'expired' — a replay finds nothing to change and this
    returns status="already_paid" instead.

    Returns {"status": "ok"|"already_paid"|"not_found"|"resurrect_failed",
    "appointment_id": str|None, "appointment": dict|None}.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        payment_row = await conn.fetchrow(
            "UPDATE payments SET status = 'paid', paid_at = now(), val_id = $2, raw = $3::jsonb "
            "WHERE id = $1::uuid AND status IN ('initiated', 'expired') "
            "RETURNING appointment_id::text AS appointment_id, kind, account_id",
            payment_id, val_id, json.dumps(raw, default=str),
        )
        if payment_row is None:
            existing = await conn.fetchrow(
                "SELECT status, appointment_id::text AS appointment_id "
                "FROM payments WHERE id = $1::uuid", payment_id,
            )
            if existing and existing["status"] == "paid":
                return {"status": "already_paid", "appointment_id": existing["appointment_id"], "appointment": None}
            return {"status": "not_found", "appointment_id": None, "appointment": None}

        # A patient-subscription payment has no linked appointment — it extends
        # the buyer's premium horizon instead.
        if payment_row["kind"] == "patient_subscription":
            if payment_row["account_id"] is not None:
                await conn.execute(
                    "UPDATE patient_accounts SET plan = 'premium', "
                    "premium_until = GREATEST(now(), COALESCE(premium_until, now())) "
                    "+ make_interval(days => $2) WHERE id = $1",
                    payment_row["account_id"], settings.patient_subscription_days,
                )
            return {"status": "ok", "appointment_id": None, "appointment": None, "kind": "patient_subscription"}

        appointment_id = payment_row["appointment_id"]
        if appointment_id is None:
            return {"status": "ok", "appointment_id": None, "appointment": None}

        async def _load(appt_id: str) -> dict:
            row = await conn.fetchrow(
                "SELECT clinic_id, doctor_id, scheduled_at, patient_name, patient_mobile, "
                "serial_number, patient_age FROM appointments WHERE id = $1::uuid",
                appt_id,
            )
            tz_row = await conn.fetchrow(
                "SELECT timezone FROM clinics WHERE id = $1", row["clinic_id"]
            )
            tz = _resolve_tz((tz_row["timezone"] if tz_row else None) or "UTC")
            return {
                **dict(row),
                "patient_name": decrypt_field(row["patient_name"]),
                "patient_mobile": decrypt_field(row["patient_mobile"]),
                "slot_label": _format_label(row["scheduled_at"].astimezone(tz)),
            }

        confirmed = await conn.fetchval(
            "UPDATE appointments SET status = 'confirmed', payment_expires_at = NULL, "
            "updated_at = now() WHERE id = $1::uuid AND status = 'pending_payment' "
            "RETURNING id::text",
            appointment_id,
        )
        if confirmed:
            appt = await _load(appointment_id)
            await _record_appointment_event(
                appointment_id=appointment_id, clinic_id=appt["clinic_id"],
                event_type="payment_confirmed", from_status="pending_payment",
                to_status="confirmed", actor_role="system",
            )
            return {"status": "ok", "appointment_id": appointment_id, "appointment": appt}

        # Not pending anymore — either already confirmed some other way, or
        # its TTL expired to 'cancelled' before this late payment landed.
        # Try to resurrect a cancelled hold IF the slot is still free
        # (someone else may have taken it in the meantime).
        try:
            resurrected = await conn.fetchval(
                "UPDATE appointments SET status = 'confirmed', payment_expires_at = NULL, "
                "cancelled_at = NULL, updated_at = now() "
                "WHERE id = $1::uuid AND status = 'cancelled' RETURNING id::text",
                appointment_id,
            )
        except asyncpg.UniqueViolationError:
            resurrected = None
        if resurrected:
            appt = await _load(appointment_id)
            await _record_appointment_event(
                appointment_id=appointment_id, clinic_id=appt["clinic_id"],
                event_type="payment_confirmed_after_expiry", to_status="confirmed",
                actor_role="system",
            )
            return {"status": "ok", "appointment_id": appointment_id, "appointment": appt}

        # The slot was taken by someone else before this late payment landed
        # — money was collected but the booking can't be honoured. Flag it
        # for a platform admin to refund (surfaced in the payments dashboard).
        await conn.execute(
            "UPDATE payments SET raw = COALESCE(raw, '{}'::jsonb) || '{\"refund_needed\": true}'::jsonb "
            "WHERE id = $1::uuid", payment_id,
        )
        return {"status": "resurrect_failed", "appointment_id": appointment_id, "appointment": None}


async def resolve_booking_fee(clinic_id: int, account_id: Optional[int]) -> int:
    """The platform service fee (৳) for one booking in this department.

    0 for a telephony/anonymous booking (account_id is None — they pay at
    the hospital desk, never through the gateway). 0 for a premium/trial
    patient (subscribers skip per-booking fees). Otherwise the hospital's own
    booking_fee if set, else the platform default.
    """
    if not account_id:
        return 0
    pool = await get_pool()
    async with pool.acquire() as conn:
        account = await conn.fetchrow(
            f"SELECT {_PATIENT_ACCOUNT_COLS} FROM patient_accounts WHERE id = $1",
            account_id,
        )
        if account and patient_tier(dict(account)) in ("premium", "trial"):
            return 0
        fee = await conn.fetchval(
            "SELECT h.booking_fee FROM clinics c JOIN hospitals h ON h.id = c.hospital_id "
            "WHERE c.id = $1",
            clinic_id,
        )
    if fee is not None:
        return int(fee)
    return int(settings.booking_fee_default)


async def sweep_expired_payments() -> int:
    """Cancel pending_payment appointments whose TTL passed and mark their
    still-initiated payments expired. Returns how many appointments were
    freed. Meant to run on a short interval (see api/app.py) — a 15-minute
    hold shouldn't sit blocking a slot for an hour until the next reminder
    sweep."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        expired_ids = await conn.fetch(
            "UPDATE appointments SET status = 'cancelled', cancelled_at = now(), "
            "cancel_reason = 'payment_timeout', updated_at = now() "
            "WHERE status = 'pending_payment' AND payment_expires_at < now() "
            "RETURNING id::text AS id, clinic_id",
        )
        if expired_ids:
            await conn.executemany(
                "UPDATE payments SET status = 'expired' "
                "WHERE appointment_id = $1::uuid AND status = 'initiated'",
                [(r["id"],) for r in expired_ids],
            )
    for r in expired_ids:
        await _record_appointment_event(
            appointment_id=r["id"], clinic_id=r["clinic_id"],
            event_type="cancelled", to_status="cancelled",
            actor_role="system", note="payment_timeout",
        )
    return len(expired_ids)


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
        # Clash only against the SAME doctor's slots — a different doctor being
        # busy at this time is irrelevant (uq_confirmed_slot is per-doctor).
        # `IS NOT DISTINCT FROM` matches NULL==NULL for clinic-level slots.
        clash = await conn.fetchval(
            "SELECT 1 FROM appointments "
            f"WHERE clinic_id = $1 AND scheduled_at = $2 AND ({_OPEN_SLOT_STATUS_SQL}) "
            "AND id <> $3::uuid "
            "AND doctor_id IS NOT DISTINCT FROM "
            "    (SELECT doctor_id FROM appointments WHERE id = $3::uuid)",
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
# no outgoing edges. pending_payment can only become confirmed (payment
# succeeded) or cancelled (TTL expiry or the patient abandoning it) — never
# checked_in/completed/no_show directly.
_STATUS_TRANSITIONS: dict[str, set[str]] = {
    "pending_payment": {"confirmed", "cancelled"},
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
    correctly per tenant.

    SMS reminders are a premium perk: a booking tied to a FREE-tier patient
    account is skipped. Telephony / walk-in bookings (no linked account) always
    get a reminder — those patients have no portal to check."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT a.id::text AS id, a.clinic_id, a.patient_name, a.patient_mobile, "
            "a.scheduled_at, "
            "to_char(a.scheduled_at AT TIME ZONE c.timezone, 'DD Mon YYYY HH24:MI') AS slot_iso, "
            "c.name AS clinic_name, c.doctor_name "
            "FROM appointments a JOIN clinics c ON c.id = a.clinic_id "
            "LEFT JOIN patients p ON p.id = a.patient_id "
            "LEFT JOIN patient_accounts pa ON pa.id = p.account_id "
            "WHERE a.status = 'confirmed' "
            "AND a.reminder_sent_at IS NULL "
            "AND a.scheduled_at BETWEEN now() + INTERVAL '23 hours' "
            "                       AND now() + INTERVAL '25 hours' "
            "AND (pa.id IS NULL OR pa.premium_until > now() OR pa.trial_ends_at > now())"
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
    "fee_new, fee_followup, created_at, (photo IS NOT NULL) AS has_photo"
)
_DOCTOR_EDITABLE = (
    "name", "specialty", "degrees", "description", "phone", "is_primary",
    "fee_new", "fee_followup",
)
# Editable fields where an explicit None is a real value (clear the fee), not
# "field omitted" — update_doctor drops None for everything else.
_DOCTOR_NULLABLE = {"fee_new", "fee_followup"}


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
    fee_new: Optional[int] = None, fee_followup: Optional[int] = None,
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
                "(clinic_id, name, specialty, degrees, description, phone, is_primary, "
                "fee_new, fee_followup) "
                f"VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING {_DOCTOR_COLS}",
                clinic_id, name, specialty, degrees, description, phone, make_primary,
                fee_new, fee_followup,
            )
            await _resync_primary(conn, clinic_id)
    return dict(row)


async def update_doctor(clinic_id: int, doctor_id: int, **fields) -> Optional[dict]:
    updates = {
        k: v for k, v in fields.items()
        if k in _DOCTOR_EDITABLE and (v is not None or k in _DOCTOR_NULLABLE)
    }
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
# Marketplace: cross-hospital doctor search + patient reviews
# --------------------------------------------------------------------------- #

# Doctor row shape shared by the platform search and the public detail view:
# profile fields + department/hospital names + published-review aggregates.
_SEARCH_SELECT = """
SELECT d.id, d.clinic_id, d.name, d.specialty, d.degrees, d.description,
       d.fee_new, d.fee_followup, (d.photo IS NOT NULL) AS has_photo,
       c.name AS department_name, h.id AS hospital_id, h.name AS hospital_name,
       COALESCE(r.avg_rating, 0)::float AS avg_rating,
       COALESCE(r.review_count, 0) AS review_count
FROM doctors d
JOIN clinics c ON d.clinic_id = c.id
JOIN hospitals h ON c.hospital_id = h.id
LEFT JOIN (
    SELECT doctor_id, ROUND(AVG(rating)::numeric, 1) AS avg_rating,
           COUNT(*) AS review_count
    FROM doctor_reviews WHERE status = 'published'
    GROUP BY doctor_id
) r ON r.doctor_id = d.id
"""

_SEARCH_SORTS = {
    "rating": "avg_rating DESC, review_count DESC, d.id",
    "fee": "d.fee_new ASC NULLS LAST, avg_rating DESC, d.id",
    # "available" is sorted by the API layer after next-slot computation.
    "available": "avg_rating DESC, review_count DESC, d.id",
}


async def search_doctors_platform(
    q: str = "",
    specialty: Optional[str] = None,
    hospital_id: Optional[int] = None,
    max_fee: Optional[int] = None,
    sort: str = "rating",
    limit: int = 20,
    offset: int = 0,
) -> list[dict]:
    """Cross-hospital doctor search for the marketplace portal and the agent.

    `q` matches doctor name, specialty, department, or hospital (ILIKE);
    `max_fee` filters on the new-patient fee and excludes doctors with no fee
    set (an unknown fee can't satisfy a budget). Plain ILIKE — pilot scale.
    """
    order = _SEARCH_SORTS.get(sort, _SEARCH_SORTS["rating"])
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            _SEARCH_SELECT
            + f"""
WHERE ($1 = '' OR d.name ILIKE '%' || $1 || '%' OR d.specialty ILIKE '%' || $1 || '%'
       OR c.name ILIKE '%' || $1 || '%' OR h.name ILIKE '%' || $1 || '%')
  AND ($2::text IS NULL OR d.specialty ILIKE $2)
  AND ($3::int IS NULL OR h.id = $3)
  AND ($4::int IS NULL OR d.fee_new <= $4)
  AND {_HOSPITAL_VISIBLE_SQL}
"""
            + f"ORDER BY {order} LIMIT $5 OFFSET $6",
            q.strip(), specialty, hospital_id, max_fee, limit, offset,
        )
    return [dict(r) for r in rows]


async def get_doctor_public(doctor_id: int) -> Optional[dict]:
    """One doctor in the marketplace row shape (for the portal detail page).

    None for a doctor at a billing-suspended hospital — the only route into
    this function is the patient portal, so it's never reached for admin
    purposes, and a lapsed hospital's booking page must 404 like the
    hospital never existed."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            _SEARCH_SELECT + f"WHERE d.id = $1 AND {_HOSPITAL_VISIBLE_SQL}", doctor_id
        )
    return dict(row) if row else None


async def list_specialties() -> list[dict]:
    """Distinct doctor specialties with counts — the portal's category tiles."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT specialty, COUNT(*) AS doctor_count FROM doctors "
            "WHERE specialty <> '' GROUP BY specialty "
            "ORDER BY doctor_count DESC, specialty"
        )
    return [dict(r) for r in rows]


async def account_review_eligible(account_id: int, doctor_id: int) -> bool:
    """A patient may review a doctor only after an appointment that happened:
    staff marked it completed, or (front desks often skip the lifecycle) it was
    a confirmed/checked-in appointment whose time has passed."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM appointments a JOIN patients p ON a.patient_id = p.id "
            "WHERE p.account_id = $1 AND a.doctor_id = $2 AND ("
            "  a.status = 'completed' "
            "  OR (a.status IN ('confirmed', 'checked_in') AND a.scheduled_at < now())"
            ") LIMIT 1",
            account_id, doctor_id,
        )
    return row is not None


async def upsert_review(
    account_id: int, doctor_id: int, rating: int, text: str = ""
) -> dict:
    """Create or update the account's single review of a doctor. Status is
    deliberately untouched on edit — a review an admin hid stays hidden."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO doctor_reviews (doctor_id, account_id, rating, text) "
            "VALUES ($1, $2, $3, $4) "
            "ON CONFLICT (doctor_id, account_id) DO UPDATE "
            "SET rating = EXCLUDED.rating, text = EXCLUDED.text, updated_at = now() "
            "RETURNING id, doctor_id, rating, text, status, created_at, updated_at",
            doctor_id, account_id, rating, text,
        )
    return dict(row)


async def get_review_for_account(account_id: int, doctor_id: int) -> Optional[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, doctor_id, rating, text, status, created_at, updated_at "
            "FROM doctor_reviews WHERE account_id = $1 AND doctor_id = $2",
            account_id, doctor_id,
        )
    return dict(row) if row else None


async def list_reviews_for_doctor(doctor_id: int, limit: int = 50) -> list[dict]:
    """Published reviews for the portal detail page, newest first."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT r.id, r.rating, r.text, r.created_at, r.updated_at, "
            "split_part(a.name, ' ', 1) AS reviewer_name "
            "FROM doctor_reviews r JOIN patient_accounts a ON r.account_id = a.id "
            "WHERE r.doctor_id = $1 AND r.status = 'published' "
            "ORDER BY r.updated_at DESC LIMIT $2",
            doctor_id, limit,
        )
    return [dict(r) for r in rows]


async def list_reviews_for_clinic(
    clinic_id: int, status: Optional[str] = None
) -> list[dict]:
    """Admin moderation listing: every review of this clinic's doctors."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT r.id, r.doctor_id, r.account_id, r.rating, r.text, r.status, "
            "r.created_at, r.updated_at, d.name AS doctor_name, "
            "a.name AS reviewer_name "
            "FROM doctor_reviews r "
            "JOIN doctors d ON r.doctor_id = d.id "
            "JOIN patient_accounts a ON r.account_id = a.id "
            "WHERE d.clinic_id = $1 AND ($2::text IS NULL OR r.status = $2) "
            "ORDER BY r.updated_at DESC",
            clinic_id, status,
        )
    return [dict(r) for r in rows]


async def set_review_status(clinic_id: int, review_id: int, status: str) -> bool:
    """Hide/publish a review — tenancy enforced in the UPDATE itself so an
    admin can only moderate reviews of their own clinic's doctors."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE doctor_reviews r SET status = $3, updated_at = now() "
            "FROM doctors d "
            "WHERE r.id = $1 AND r.doctor_id = d.id AND d.clinic_id = $2 "
            "RETURNING r.id",
            review_id, clinic_id, status,
        )
    return row is not None


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
    *, clinic_id: Optional[int], session_id: str, channel: str, role: str, text: str,
    channel_identifier: Optional[str] = None, hospital_id: Optional[int] = None,
) -> None:
    """Best-effort append of one turn. Never raises into the caller.

    clinic_id may be None — a unified platform-mode thread can log turns
    before any department is chosen (migration 0025 made the column
    nullable). hospital_id, when known, lets a hospital admin see their
    slice of a cross-hospital thread even for those pre-department turns.
    """
    if not session_id or not text:
        return
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            try:
                await conn.execute(
                    "INSERT INTO conversation_log "
                    "(clinic_id, hospital_id, session_id, channel, role, text, channel_identifier) "
                    "VALUES ($1,$2,$3,$4,$5,$6,$7)",
                    clinic_id, hospital_id, session_id, channel or "text", role, text, channel_identifier,
                )
            except asyncpg.UndefinedColumnError:
                # hospital_id (0025) or channel_identifier (0010) not yet migrated.
                try:
                    await conn.execute(
                        "INSERT INTO conversation_log (clinic_id, session_id, channel, role, text, channel_identifier) "
                        "VALUES ($1,$2,$3,$4,$5,$6)",
                        clinic_id, session_id, channel or "text", role, text, channel_identifier,
                    )
                except asyncpg.UndefinedColumnError:
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


async def delete_conversation_by_session(session_id: str) -> int:
    """Delete all logged turns for one session, regardless of clinic_id —
    a unified platform thread's turns are scattered across clinic_id values
    (including NULL), so the "new conversation" wipe can't be clinic-scoped."""
    if not session_id:
        return 0
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM conversation_log WHERE session_id = $1", session_id,
        )
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


async def list_conversations_for_hospital(
    hospital_id: int, limit: int = 50, offset: int = 0
) -> list[dict]:
    """Recent conversations across a whole hospital: its own clinics' turns
    PLUS hospital-level turns logged before a department was chosen (a slice
    of a unified pt-acc platform thread) — one row per session."""
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
            WHERE hospital_id = $1
               OR clinic_id IN (SELECT id FROM clinics WHERE hospital_id = $1)
            GROUP BY session_id
            ORDER BY last_at DESC
            LIMIT $2 OFFSET $3
            """,
            hospital_id, limit, offset,
        )
    return [dict(r) for r in rows]


async def get_conversation_for_hospital(hospital_id: int, session_id: str) -> list[dict]:
    """Full ordered transcript for one session, scoped to a hospital's own
    clinics + hospital-level turns — the same slicing as
    list_conversations_for_hospital, for one session."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT role, text, channel, created_at FROM conversation_log
            WHERE session_id = $2
              AND (hospital_id = $1 OR clinic_id IN (SELECT id FROM clinics WHERE hospital_id = $1))
            ORDER BY created_at, id
            """,
            hospital_id, session_id,
        )
    return [dict(r) for r in rows]


async def escalation_open_for_staff(
    session_id: str, clinic_id: Optional[int], hospital_id: Optional[int]
) -> bool:
    """True when this session has an OPEN escalation assigned to the staff
    member's clinic or hospital — the gate for replying into a patient's
    unified cross-hospital thread (see api/routes/conversations.py reply)."""
    if clinic_id is None and hospital_id is None:
        return False
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM escalations WHERE session_id = $1 AND status = 'open' AND "
            "(($2::int IS NOT NULL AND clinic_id = $2) OR ($3::int IS NOT NULL AND hospital_id = $3)) "
            "LIMIT 1",
            session_id, clinic_id, hospital_id,
        )
    return row is not None


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
    """Every hospital, any billing state — for the admin console (a hospital
    admin/platform admin must still manage a lapsed hospital's own account)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT {_HOSPITAL_COLS} FROM hospitals ORDER BY id"
        )
    return [dict(r) for r in rows]


# "Block hospitals only": a hospital whose subscription lapses past its grace
# period disappears from patient-facing search/browse/booking — this is the
# single predicate every one of those surfaces filters on. `status` is the
# pre-existing operational flag (a hospital admin can self-suspend);
# `billing_status` (migration 0026) is separate — only "suspended" (past
# grace, not merely "past_due") hides a hospital, so a hospital stays
# discoverable during its grace window.
_HOSPITAL_VISIBLE_SQL = "h.status = 'active' AND h.billing_status <> 'suspended'"


async def list_hospitals_public() -> list[dict]:
    """Hospitals visible to patients — excludes billing-suspended ones."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT {_HOSPITAL_COLS} FROM hospitals h "
            f"WHERE {_HOSPITAL_VISIBLE_SQL} ORDER BY id"
        )
    return [dict(r) for r in rows]


async def hospital_bookable(hospital_id: int) -> bool:
    """True unless this hospital is billing-suspended or operationally
    suspended — the guard for the direct-booking endpoint and the portal
    voice-token mint (both reachable without going through search first)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchval(
            f"SELECT 1 FROM hospitals h WHERE h.id = $1 AND {_HOSPITAL_VISIBLE_SQL}",
            hospital_id,
        )
    return row is not None


# --------------------------------------------------------------------------- #
# Hospital subscriptions (monthly platform billing)
# --------------------------------------------------------------------------- #

async def start_hospital_free_trial(
    hospital_id: int, *, monthly_fee: int, trial_days: int
) -> Optional[dict]:
    """Seed a newly signed-up hospital's subscription with a FREE first
    period — no payment collected. The billing sweep treats it exactly like
    any paid period: once current_period_end passes, it becomes past_due
    (grace window) then suspended (hidden from patient search/booking) if
    never paid. No-op (returns None) if a subscription already exists."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO hospital_subscriptions "
            "(hospital_id, monthly_fee, status, current_period_start, current_period_end) "
            "VALUES ($1, $2, 'active', now(), now() + make_interval(days => $3)) "
            "ON CONFLICT (hospital_id) DO NOTHING "
            "RETURNING hospital_id, monthly_fee, status, current_period_start, current_period_end",
            hospital_id, monthly_fee, trial_days,
        )
    return dict(row) if row else None


async def get_hospital_subscription(hospital_id: int) -> Optional[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT hospital_id, monthly_fee, status, current_period_start, "
            "current_period_end, grace_days, created_at, updated_at "
            "FROM hospital_subscriptions WHERE hospital_id = $1",
            hospital_id,
        )
    return dict(row) if row else None


async def sweep_hospital_billing() -> int:
    """Hourly billing state machine (piggybacks the reminder loop):
    active subscriptions past their period end become past_due (grace
    window); past_due subscriptions past grace_days become suspended.
    Mirrors the result onto hospitals.billing_status — the single predicate
    patient search/browse/booking filter on. Returns how many changed."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            UPDATE hospital_subscriptions s SET
                status = CASE
                    WHEN s.status = 'active' AND s.current_period_end < now() THEN 'past_due'
                    ELSE 'suspended'
                END,
                updated_at = now()
            WHERE s.current_period_end IS NOT NULL AND (
                (s.status = 'active' AND s.current_period_end < now())
                OR (s.status = 'past_due'
                    AND s.current_period_end < now() - (s.grace_days || ' days')::interval)
            )
            RETURNING s.hospital_id, s.status
            """
        )
        if rows:
            await conn.executemany(
                "UPDATE hospitals SET billing_status = $2 WHERE id = $1",
                [(r["hospital_id"], r["status"]) for r in rows],
            )
    return len(rows)


async def mark_subscription_invoice_paid(hospital_id: int, *, method: str = "manual") -> dict:
    """Record payment for the current billing period and advance it by one
    month, reactivating the hospital if it had lapsed. Creates the first
    period if none exists yet (e.g. a hospital provisioned via the old
    platform-key flow, with no trial)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        sub = await conn.fetchrow(
            "SELECT monthly_fee, current_period_end FROM hospital_subscriptions "
            "WHERE hospital_id = $1", hospital_id,
        )
        if sub is None:
            raise ValueError(f"No subscription exists for hospital {hospital_id}")
        base = sub["current_period_end"]
        # Extend from the later of "now" or the existing period end, so
        # paying early doesn't shorten a still-active period.
        new_end = await conn.fetchval(
            "SELECT GREATEST(now(), $1::timestamptz) + interval '1 month'", base,
        )
        row = await conn.fetchrow(
            "UPDATE hospital_subscriptions SET status = 'active', "
            "current_period_start = now(), current_period_end = $2, updated_at = now() "
            "WHERE hospital_id = $1 "
            "RETURNING hospital_id, monthly_fee, status, current_period_start, current_period_end",
            hospital_id, new_end,
        )
        await conn.execute(
            "UPDATE hospitals SET billing_status = 'active' WHERE id = $1", hospital_id
        )
        invoice = await conn.fetchrow(
            "INSERT INTO subscription_invoices "
            "(hospital_id, period_start, period_end, amount, status, method, paid_at) "
            "VALUES ($1, now()::date, $2::date, $3, 'paid', $4, now()) "
            "RETURNING id, hospital_id, period_start, period_end, amount, status, method, paid_at",
            hospital_id, new_end, sub["monthly_fee"], method,
        )
    return {"subscription": dict(row), "invoice": dict(invoice)}


async def platform_revenue_stats() -> dict:
    """Cross-tenant revenue snapshot for the platform-admin dashboard: paid
    booking-fee and patient-subscription totals, subscriber/trial counts, and
    the count of open platform-level (unassigned) escalations."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rev = await conn.fetchrow(
            "SELECT "
            "COALESCE(SUM(amount) FILTER (WHERE kind='booking_fee' AND status='paid'),0) AS booking_fee_revenue, "
            "COALESCE(SUM(amount) FILTER (WHERE kind='patient_subscription' AND status='paid'),0) AS patient_sub_revenue, "
            "COUNT(*) FILTER (WHERE status='paid') AS paid_count, "
            "COUNT(*) FILTER (WHERE (raw->>'refund_needed') = 'true' AND status <> 'refunded') AS refunds_pending "
            "FROM payments"
        )
        subs = await conn.fetchrow(
            "SELECT "
            "COUNT(*) FILTER (WHERE premium_until > now()) AS premium, "
            "COUNT(*) FILTER (WHERE (premium_until IS NULL OR premium_until <= now()) "
            "                       AND trial_ends_at > now()) AS trialing "
            "FROM patient_accounts"
        )
        open_esc = await conn.fetchval(
            "SELECT COUNT(*) FROM escalations "
            "WHERE status = 'open' AND clinic_id IS NULL AND hospital_id IS NULL"
        )
    return {
        "booking_fee_revenue": int(rev["booking_fee_revenue"]),
        "patient_sub_revenue": int(rev["patient_sub_revenue"]),
        "paid_count": int(rev["paid_count"]),
        "refunds_pending": int(rev["refunds_pending"]),
        "subscribers_premium": int(subs["premium"]),
        "subscribers_trialing": int(subs["trialing"]),
        "open_platform_escalations": int(open_esc or 0),
    }


async def list_hospitals_admin() -> list[dict]:
    """Every hospital (unfiltered — admins see suspended ones too) with its
    subscription/billing state, paid booking-fee revenue, and outstanding
    subscription dues (sum of unpaid invoices)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT h.id, h.name, h.slug, h.billing_status, h.booking_fee,
                   s.status AS subscription_status, s.monthly_fee,
                   s.current_period_end,
                   COALESCE(fee.revenue, 0) AS fee_revenue,
                   COALESCE(fee.bookings, 0) AS paid_bookings,
                   COALESCE(due.amount, 0) AS dues
            FROM hospitals h
            LEFT JOIN hospital_subscriptions s ON s.hospital_id = h.id
            LEFT JOIN (
                SELECT hospital_id,
                       SUM(amount) AS revenue,
                       COUNT(*)    AS bookings
                FROM payments
                WHERE kind = 'booking_fee' AND status = 'paid'
                GROUP BY hospital_id
            ) fee ON fee.hospital_id = h.id
            LEFT JOIN (
                SELECT hospital_id, SUM(amount) AS amount
                FROM subscription_invoices WHERE status = 'due'
                GROUP BY hospital_id
            ) due ON due.hospital_id = h.id
            ORDER BY fee_revenue DESC, h.name ASC
            """
        )
    return [dict(r) for r in rows]


async def list_payments(
    *, kind: Optional[str] = None, status: Optional[str] = None,
    hospital_id: Optional[int] = None, limit: int = 100,
) -> list[dict]:
    """Recent payments for the admin ledger, optionally filtered by kind /
    status / hospital. Never returns raw gateway blobs — just the ledger view."""
    pool = await get_pool()
    clauses, params = [], []
    if kind:
        params.append(kind); clauses.append(f"p.kind = ${len(params)}")
    if status:
        params.append(status); clauses.append(f"p.status = ${len(params)}")
    if hospital_id is not None:
        params.append(hospital_id); clauses.append(f"p.hospital_id = ${len(params)}")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    async with (await get_pool()).acquire() as conn:
        rows = await conn.fetch(
            f"SELECT p.id::text AS id, p.kind, p.amount, p.currency, p.status, "
            f"p.provider, p.hospital_id, h.name AS hospital_name, "
            f"p.appointment_id::text AS appointment_id, p.account_id, "
            f"p.created_at, p.paid_at, "
            f"COALESCE((p.raw->>'refund_needed') = 'true', false) AS refund_needed "
            f"FROM payments p LEFT JOIN hospitals h ON h.id = p.hospital_id "
            f"{where} ORDER BY p.created_at DESC LIMIT ${len(params)}",
            *params,
        )
    return [dict(r) for r in rows]


async def refund_payment(payment_id: str, *, note: str = "") -> Optional[dict]:
    """Mark a paid payment refunded (pilot: records the intent + note; the
    actual bKash/Nagad refund is done by hand). Returns the updated row or None
    if the payment isn't in a refundable state."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE payments SET status = 'refunded', "
            "raw = COALESCE(raw, '{}'::jsonb) "
            "      || jsonb_build_object('refunded', true, 'refund_note', $2::text) "
            "WHERE id = $1::uuid AND status = 'paid' "
            "RETURNING id::text AS id, status",
            payment_id, note,
        )
    return dict(row) if row else None


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


async def create_hospital_tenant(
    *, slug: str, name: str, doctor_name: str = "Doctor", doctor_phone: str = "",
    availability_days_ahead: int = 7, admin_email: str, admin_password_hash: str,
    monthly_fee: Optional[int] = None, trial_days: Optional[int] = None,
) -> dict:
    """Provision a whole hospital tenant in ONE transaction: hospital + default
    clinic + web channel + admin user + free-trial subscription.

    Atomic on purpose. The old signup path ran these as four independent
    commits, so a failure partway (most commonly a duplicate admin email after
    the hospital+clinic were already committed) left an orphaned, admin-less
    hospital that (a) permanently blocked that hospital name from ever signing
    up again and (b) showed up in patient search with no doctors. Here a
    UniqueViolationError on the slug or email aborts the whole transaction —
    nothing is committed — and propagates for the caller to map to a 409.

    Returns {"user", "hospital", "clinic"} on success.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            hosp = await conn.fetchrow(
                "INSERT INTO hospitals (slug, name, address, license_number, timezone) "
                f"VALUES ($1, $2, '', '', 'Asia/Dhaka') RETURNING {_HOSPITAL_COLS}",
                slug, name,
            )
            clinic = await conn.fetchrow(
                "INSERT INTO clinics (slug, name, doctor_name, doctor_phone, "
                "availability_days_ahead, hospital_id) VALUES ($1,$2,$3,$4,$5,$6) "
                f"RETURNING {_CLINIC_COLS}",
                slug, name, doctor_name, doctor_phone, availability_days_ahead, hosp["id"],
            )
            await conn.execute(
                "INSERT INTO channels (clinic_id, kind, identifier) "
                "VALUES ($1, 'web', $2) ON CONFLICT (kind, identifier) DO NOTHING",
                clinic["id"], slug,
            )
            user = await conn.fetchrow(
                "INSERT INTO users (clinic_id, hospital_id, email, password_hash, role) "
                "VALUES ($1,$2,$3,$4,'hospital_admin') "
                "RETURNING id, clinic_id, hospital_id, email, role",
                clinic["id"], hosp["id"], admin_email, admin_password_hash,
            )
            # Free-trial subscription only when a trial is requested (public
            # self-signup). Platform-admin-created tenants pass trial_days=None
            # and get no subscription row, matching the legacy behavior.
            if trial_days is not None:
                await conn.execute(
                    "INSERT INTO hospital_subscriptions "
                    "(hospital_id, monthly_fee, status, current_period_start, current_period_end) "
                    "VALUES ($1, $2, 'active', now(), now() + make_interval(days => $3)) "
                    "ON CONFLICT (hospital_id) DO NOTHING",
                    hosp["id"], monthly_fee or 0, trial_days,
                )
    return {"user": dict(user), "hospital": dict(hosp), "clinic": dict(clinic)}


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
            # Serialise numbering per (department, date). `FOR UPDATE` can't be
            # used here — Postgres forbids it with aggregates, and on the day's
            # first token it would lock zero rows anyway (two callers would both
            # read MAX=0 and collide). A transaction-scoped advisory lock is the
            # same pattern book_appointment/create_patient use for serials/MRNs.
            lock_key = department_id * 1_000_000 + (date_obj.toordinal() % 1_000_000)
            await conn.execute("SELECT pg_advisory_xact_lock($1)", lock_key)
            max_num = await conn.fetchval(
                "SELECT COALESCE(MAX(token_number), 0) FROM appointment_tokens "
                "WHERE department_id = $1 AND token_date = $2",
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

_PATIENT_ACCOUNT_COLS = (
    "id, email, phone, name, created_at, plan, trial_ends_at, premium_until, "
    "phone_verified_at"
)


def patient_tier(account: dict) -> str:
    """Effective plan tier for an account row: 'premium' > 'trial' > 'free'.

    A live subscription (premium_until in the future) wins; otherwise a signup
    trial that hasn't elapsed grants FULL premium access (including the ৳0
    booking fee); otherwise the account is on the free tier.
    """
    now = datetime.now(timezone.utc)
    premium_until = account.get("premium_until")
    if premium_until and premium_until > now:
        return "premium"
    trial_ends_at = account.get("trial_ends_at")
    if trial_ends_at and trial_ends_at > now:
        return "trial"
    return "free"


async def create_patient_account(
    *, email: str, password_hash: str, name: str = "", phone: str = "",
) -> Optional[dict]:
    """Create a patient login account with a fresh full-access trial.

    Returns None if the email is taken."""
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO patient_accounts (email, password_hash, name, phone, trial_ends_at) "
                f"VALUES ($1,$2,$3,$4, now() + make_interval(days => $5)) "
                f"RETURNING {_PATIENT_ACCOUNT_COLS}",
                email, password_hash, name, phone, settings.patient_trial_days,
            )
        return dict(row)
    except asyncpg.UniqueViolationError:
        return None


async def get_agent_bookings_used(account_id: int, period: Optional[str] = None) -> int:
    """How many agent (chat/voice) bookings this account made in `period`
    (a 'YYYY-MM' calendar month, default = current UTC month)."""
    period = period or datetime.now(timezone.utc).strftime("%Y-%m")
    pool = await get_pool()
    async with pool.acquire() as conn:
        used = await conn.fetchval(
            "SELECT agent_bookings FROM patient_usage WHERE account_id = $1 AND period = $2",
            account_id, period,
        )
    return int(used or 0)


async def increment_agent_bookings(account_id: int, period: Optional[str] = None) -> int:
    """Bump this account's agent-booking counter for the month; returns the new
    total. Called at exactly one point — a successful agent booking."""
    period = period or datetime.now(timezone.utc).strftime("%Y-%m")
    pool = await get_pool()
    async with pool.acquire() as conn:
        total = await conn.fetchval(
            "INSERT INTO patient_usage (account_id, period, agent_bookings) "
            "VALUES ($1, $2, 1) "
            "ON CONFLICT (account_id, period) DO UPDATE "
            "SET agent_bookings = patient_usage.agent_bookings + 1 "
            "RETURNING agent_bookings",
            account_id, period,
        )
    return int(total)


async def activate_patient_subscription(account_id: int, *, days: Optional[int] = None) -> dict:
    """Extend an account's premium horizon by one prepaid period.

    Stacks on any remaining time (GREATEST(now, premium_until) + days) so a
    renewal made early never loses days, and flips the plan to 'premium'.
    Returns the updated account row."""
    days = days if days is not None else settings.patient_subscription_days
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE patient_accounts SET plan = 'premium', "
            "premium_until = GREATEST(now(), COALESCE(premium_until, now())) "
            "+ make_interval(days => $2) "
            f"WHERE id = $1 RETURNING {_PATIENT_ACCOUNT_COLS}",
            account_id, days,
        )
    return dict(row) if row else {}


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


# --- One-time phone verification (premium voice-calling gate) ---

async def upsert_phone_verification(
    account_id: int, phone: str, code_hash: str, expires_at
) -> None:
    """Store the pending OTP for this account (at most one; a re-send replaces
    the previous code and resets the attempt counter)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO phone_verifications (account_id, phone, code_hash, expires_at)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (account_id) DO UPDATE SET
                phone = EXCLUDED.phone, code_hash = EXCLUDED.code_hash,
                expires_at = EXCLUDED.expires_at, attempts = 0, created_at = now()
            """,
            account_id, phone, code_hash, expires_at,
        )


async def get_phone_verification(account_id: int) -> Optional[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT account_id, phone, code_hash, expires_at, attempts, created_at "
            "FROM phone_verifications WHERE account_id = $1",
            account_id,
        )
    return dict(row) if row else None


async def increment_phone_verification_attempts(account_id: int) -> int:
    """Bump the failed-attempt counter; returns the new count."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "UPDATE phone_verifications SET attempts = attempts + 1 "
            "WHERE account_id = $1 RETURNING attempts",
            account_id,
        ) or 0


async def confirm_phone_verification(account_id: int, phone: str) -> str:
    """Mark the account's phone verified (single tx). Returns:

    "ok"          — phone set + phone_verified_at stamped, pending OTP deleted.
    "phone_taken" — another account already verified this number (the partial
                    unique index blocks it — one number = one account, the
                    trial-farming guard).
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            try:
                await conn.execute(
                    "UPDATE patient_accounts SET phone = $2, phone_verified_at = now() "
                    "WHERE id = $1",
                    account_id, phone,
                )
            except asyncpg.UniqueViolationError:
                return "phone_taken"
            await conn.execute(
                "DELETE FROM phone_verifications WHERE account_id = $1", account_id
            )
    return "ok"


async def get_verified_account_by_phone(phone: str) -> Optional[dict]:
    """The account that VERIFIED this number, or None. Used by the voice worker
    to match SIP caller-ID; only verified numbers count (unverified signup
    phones are self-reported and spoofable by anyone who knows them)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {_PATIENT_ACCOUNT_COLS} FROM patient_accounts "
            "WHERE phone = $1 AND phone_verified_at IS NOT NULL",
            phone,
        )
    return dict(row) if row else None


async def list_appointments_for_account(account_id: int) -> list[dict]:
    """Every appointment booked under any of this account's hospital patient
    records, newest first, enriched with hospital/department/doctor labels.
    Held (pending_payment) bookings sort first so a free-tier history cap
    applied downstream never hides an unpaid hold."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT a.id::text AS id, a.patient_name, a.patient_mobile,
                   a.scheduled_at, a.duration_mins, a.status, a.serial_number,
                   a.created_at, a.payment_expires_at,
                   c.id   AS clinic_id,   c.name AS department_name,
                   h.id   AS hospital_id, h.name AS hospital_name,
                   d.id AS doctor_id, d.name AS doctor_name
            FROM appointments a
            JOIN patients p   ON a.patient_id = p.id
            LEFT JOIN clinics c   ON a.clinic_id = c.id
            LEFT JOIN hospitals h ON c.hospital_id = h.id
            LEFT JOIN doctors d   ON a.doctor_id = d.id
            WHERE p.account_id = $1
            ORDER BY (a.status = 'pending_payment') DESC, a.scheduled_at DESC
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
    """Cancel a confirmed OR still-pending-payment appointment that belongs to
    one of this account's hospital patient records — a patient can abandon
    an unpaid booking, not just a confirmed one. True if a row changed
    (ownership enforced in SQL)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE appointments "
            "SET status = 'cancelled', cancelled_at = now(), updated_at = now() "
            "FROM patients p "
            "WHERE appointments.id = $1::uuid AND appointments.patient_id = p.id "
            "AND p.account_id = $2 AND appointments.status IN ('confirmed', 'pending_payment') "
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
    *, clinic_id: Optional[int], session_id: str, channel: str, reason: str,
    hospital_id: Optional[int] = None,
) -> Optional[int]:
    """Record a conversation flagged for staff follow-up. Returns the row id.

    hospital_id routes a hospital-level question (no department chosen yet)
    to that hospital's queue; both clinic_id and hospital_id None is a
    pure platform-level escalation, visible only in the platform dashboard.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO escalations (clinic_id, hospital_id, session_id, channel, reason) "
            "VALUES ($1, $2, $3, $4, $5) RETURNING id",
            clinic_id, hospital_id, session_id, channel or "text", reason or "",
        )


async def list_escalations(
    *, clinic_id: Optional[int] = None, hospital_id: Optional[int] = None,
    status: str = "open", platform: bool = False,
) -> list[dict]:
    """Escalations scoped by clinic, by hospital (own clinics + hospital-level
    rows), or — platform=True — everything, including pure platform-level
    (clinic_id AND hospital_id both NULL) escalations. Newest first."""
    pool = await get_pool()
    cols = "id, clinic_id, hospital_id, session_id, channel, reason, status, created_at, resolved_at"
    async with pool.acquire() as conn:
        if platform:
            rows = await conn.fetch(
                f"SELECT {cols} FROM escalations WHERE ($1 = '' OR status = $1) "
                "ORDER BY created_at DESC LIMIT 200",
                status or "",
            )
        elif hospital_id is not None:
            rows = await conn.fetch(
                f"SELECT {cols} FROM escalations WHERE ($2 = '' OR status = $2) AND "
                "(hospital_id = $1 OR clinic_id IN (SELECT id FROM clinics WHERE hospital_id = $1)) "
                "ORDER BY created_at DESC LIMIT 100",
                hospital_id, status or "",
            )
        else:
            rows = await conn.fetch(
                f"SELECT {cols} FROM escalations WHERE clinic_id = $1 AND ($2 = '' OR status = $2) "
                "ORDER BY created_at DESC LIMIT 100",
                clinic_id, status or "",
            )
    return [dict(r) for r in rows]


async def resolve_escalation(
    escalation_id: int, *, clinic_id: Optional[int] = None,
    hospital_id: Optional[int] = None, platform: bool = False,
) -> bool:
    """Mark an escalation resolved, tenant-scoped unless platform=True. True if a row changed."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        if platform:
            result = await conn.execute(
                "UPDATE escalations SET status = 'resolved', resolved_at = now() "
                "WHERE id = $1 AND status = 'open'",
                escalation_id,
            )
        elif hospital_id is not None:
            result = await conn.execute(
                "UPDATE escalations SET status = 'resolved', resolved_at = now() "
                "WHERE id = $1 AND status = 'open' AND "
                "(hospital_id = $2 OR clinic_id IN (SELECT id FROM clinics WHERE hospital_id = $2))",
                escalation_id, hospital_id,
            )
        else:
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
