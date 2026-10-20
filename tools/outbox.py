"""Outbound message outbox — reliable, retried, de-duplicated SMS/WhatsApp.

Every SMS (tools.sms) and WhatsApp (tools.whatsapp) send goes through send():

  1. a row is written to `outbound_messages` (recipient + body encrypted),
  2. delivery is attempted INLINE, so an OTP still arrives in seconds,
  3. on a provider failure the row stays `queued` with exponential backoff and
     the backend's outbox loop (api/app.py) retries it via drain() until it is
     sent or its time-to-live passes (`expired`) — an agent reply or OTP is
     useless after minutes, a reminder is good for hours.

An `idempotency_key` makes a logical message send at most once (e.g. one
reminder per appointment) even if the caller runs twice.

Fail-safe: if the outbox table can't be written (DB down), the message is sent
directly instead — the outbox adds reliability, it must never remove it.

The successful send is what's metered against the hospital's credit wallet and
recorded in `sms_log` (the console's SMS tracking view).
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Awaitable, Callable, Optional

from config import settings
from tools.crypto import decrypt_field, encrypt_field

log = logging.getLogger(__name__)

# Retry delays (seconds) after the 1st, 2nd, ... failed attempt.
BACKOFF_SECONDS = (30, 120, 600, 1800, 3600)
MAX_ATTEMPTS = len(BACKOFF_SECONDS) + 1

# How long each kind of message stays worth delivering.
_TTL_SECONDS: dict[str, int] = {
    "agent_reply": 15 * 60,     # a chat reply an hour late is noise
    "otp": 10 * 60,             # codes expire after 10 minutes anyway
    "token": 30 * 60,           # "your serial is being called"
}
_DEFAULT_TTL_SECONDS = 24 * 3600   # confirmations, reminders, alerts

# A row left in 'sending' this long belongs to a crashed worker — requeue it.
_STUCK_SENDING = timedelta(minutes=5)


class NotConfigured(Exception):
    """The channel has no provider configured — the send is skipped, not retried."""


# channel -> async (to, body, sender_id) -> (provider_name, provider_msg_id)
Sender = Callable[[str, str, str], Awaitable[tuple[str, Optional[str]]]]


def _sender(channel: str) -> Sender:
    # Imported lazily: tools.sms / tools.whatsapp import this module.
    if channel == "sms":
        from tools.sms import provider_send
        return provider_send
    from tools.whatsapp import provider_send
    return provider_send


def _ttl(kind: str) -> int:
    return _TTL_SECONDS.get(kind, _DEFAULT_TTL_SECONDS)


async def _on_sent(channel: str, to: str, body: str, kind: str,
                   clinic_id: Optional[int], provider: str) -> None:
    """Bookkeeping for a delivered message: SMS tracking row + wallet metering."""
    from tools.database import charge_channel_usage, log_sms

    if channel == "sms":
        from utils.sms_segments import sms_segments

        await log_sms(clinic_id=clinic_id, to_number=to, body=body, kind=kind,
                      status="sent", provider=provider)
        # Gateways bill per segment: a Bangla SMS is UCS-2 (70 chars, 67 per
        # part), so a typical confirmation is 2–3 parts, not 1.
        parts = max(1, sms_segments(body))
        await charge_channel_usage(clinic_id, reason="sms", credits=settings.credit_cost_sms * parts,
                                   quantity=parts, note=kind)
        return
    await charge_channel_usage(clinic_id, reason=channel, credits=settings.credit_cost_whatsapp, note=kind)


async def _on_failed(channel: str, to: str, body: str, kind: str,
                     clinic_id: Optional[int], provider: Optional[str], error: str) -> None:
    if channel == "sms":
        from tools.database import log_sms
        await log_sms(clinic_id=clinic_id, to_number=to, body=body, kind=kind,
                      status="failed", provider=provider, error=error[:500])


async def _attempt(row: dict) -> str:
    """Try one delivery of a claimed row and persist the outcome.

    Returns 'sent', 'queued' (will retry), 'failed' or 'expired'.
    """
    from tools.database import get_pool

    channel, kind = row["channel"], row["kind"]
    to, body = decrypt_field(row["to_number"]), decrypt_field(row["body"])
    try:
        provider, msg_id = await _sender(channel)(to, body, row.get("sender_id") or "")
    except Exception as exc:
        error = str(exc)[:500] or type(exc).__name__
        attempts = row["attempts"]
        pool = await get_pool()
        async with pool.acquire() as conn:
            if attempts >= MAX_ATTEMPTS:
                status = "failed"
                await conn.execute(
                    "UPDATE outbound_messages SET status='failed', last_error=$2, "
                    "updated_at=now() WHERE id=$1", row["id"], error,
                )
            else:
                delay = BACKOFF_SECONDS[attempts - 1]
                # Past its TTL a retry is pointless — expire it instead.
                status = await conn.fetchval(
                    "UPDATE outbound_messages SET "
                    "  status = CASE WHEN now() + make_interval(secs => $3) > expires_at "
                    "                THEN 'expired' ELSE 'queued' END, "
                    "  next_attempt_at = now() + make_interval(secs => $3), "
                    "  last_error=$2, updated_at=now() "
                    "WHERE id=$1 RETURNING status", row["id"], error, float(delay),
                )
        log.warning("%s send %s (%s) attempt %d failed → %s: %s",
                    channel, row["id"], kind, attempts, status, error)
        if status in ("failed", "expired"):
            await _on_failed(channel, to, body, kind, row.get("clinic_id"), None, error)
        return status

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE outbound_messages SET status='sent', provider=$2, provider_msg_id=$3, "
            "last_error=NULL, updated_at=now() WHERE id=$1", row["id"], provider, msg_id,
        )
    await _on_sent(channel, to, body, kind, row.get("clinic_id"), provider)
    return "sent"


async def _send_direct(channel: str, to: str, body: str, kind: str,
                       clinic_id: Optional[int], sender_id: str) -> str:
    """Outbox unavailable (DB down): one unrecorded-in-outbox inline attempt."""
    try:
        provider, _ = await _sender(channel)(to, body, sender_id)
    except NotConfigured:
        raise
    except Exception as exc:
        log.exception("%s direct send to %s failed", channel, kind)
        await _on_failed(channel, to, body, kind, clinic_id, None, str(exc))
        return "failed"
    await _on_sent(channel, to, body, kind, clinic_id, provider)
    return "sent"


async def send(
    channel: str, to: str, body: str, *, kind: str,
    clinic_id: Optional[int] = None, sender_id: str = "",
    idempotency_key: Optional[str] = None, configured: bool = True,
) -> str:
    """Queue + immediately attempt one message. Never raises.

    Returns 'sent', 'queued' (accepted; retrying), 'failed', 'expired',
    'skipped' (channel not configured) or 'duplicate' (idempotency_key seen).
    Callers that must know a message will go out (the reminder job) should
    treat 'sent' and 'queued' as accepted.
    """
    from tools.database import get_pool, log_sms

    if not configured:
        if channel == "sms":
            await log_sms(clinic_id=clinic_id, to_number=to, body=body, kind=kind,
                          status="skipped", provider="none")
        return "skipped"

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO outbound_messages "
                "  (channel, kind, to_number, body, sender_id, clinic_id, status, "
                "   attempts, expires_at, idempotency_key) "
                "VALUES ($1,$2,$3,$4,$5,$6,'sending',1, now() + make_interval(secs => $7), $8) "
                "ON CONFLICT (idempotency_key) DO NOTHING "
                "RETURNING id, channel, kind, to_number, body, sender_id, clinic_id, attempts",
                channel, kind, encrypt_field(to), encrypt_field(body), sender_id or None,
                clinic_id, float(_ttl(kind)), idempotency_key,
            )
    except Exception:
        log.warning("outbox unavailable — sending %s %s directly", channel, kind, exc_info=True)
        try:
            return await _send_direct(channel, to, body, kind, clinic_id, sender_id)
        except NotConfigured:
            return "skipped"

    if row is None:
        log.info("outbox: duplicate %s %s suppressed (key=%s)", channel, kind, idempotency_key)
        return "duplicate"
    try:
        return await _attempt(dict(row))
    except Exception:
        # Bookkeeping failed after insert; the stuck-row sweep will requeue it.
        log.exception("outbox attempt for %s crashed", row["id"])
        return "queued"


async def drain(limit: int = 50) -> int:
    """Retry due queued rows; expire stale ones; requeue crashed 'sending' rows.

    Safe to run concurrently in every worker: rows are claimed with
    FOR UPDATE SKIP LOCKED, so each is attempted by exactly one process.
    Returns the number of rows attempted.
    """
    from tools.database import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE outbound_messages SET status='queued', updated_at=now() "
            "WHERE status='sending' AND updated_at < now() - $1::interval", _STUCK_SENDING,
        )
        await conn.execute(
            "UPDATE outbound_messages SET status='expired', updated_at=now() "
            "WHERE status='queued' AND expires_at < now()"
        )
        rows = await conn.fetch(
            "UPDATE outbound_messages SET status='sending', attempts=attempts+1, updated_at=now() "
            "WHERE id IN ("
            "  SELECT id FROM outbound_messages "
            "  WHERE status='queued' AND next_attempt_at <= now() "
            "  ORDER BY next_attempt_at LIMIT $1 FOR UPDATE SKIP LOCKED"
            ") RETURNING id, channel, kind, to_number, body, sender_id, clinic_id, attempts",
            limit,
        )
    for row in rows:
        try:
            await _attempt(dict(row))
        except Exception:
            log.exception("outbox retry for %s crashed", row["id"])
    return len(rows)


async def mark_delivery(provider: str, provider_msg_id: str, delivered: bool,
                        error: str | None = None) -> bool:
    """Apply a delivery report (SMS DLR / WhatsApp status). Returns whether a
    row matched. A late 'delivered' never downgrades, a late failure never
    overrides a confirmed delivery."""
    from tools.database import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        res = await conn.execute(
            "UPDATE outbound_messages SET status=$3, last_error=COALESCE($4, last_error), "
            "updated_at=now() WHERE provider=$1 AND provider_msg_id=$2 "
            "AND status IN ('sent','sending','queued')",
            provider, provider_msg_id, "delivered" if delivered else "failed", error,
        )
    return res.endswith(" 1")


async def seen_inbound(provider: str, event_id: str) -> bool:
    """Record an inbound webhook event; True if it was ALREADY processed.

    Webhook providers retry deliveries — a retried message must not run a
    second agent turn. Fails open (returns False) if the DB is unavailable:
    a rare duplicate reply beats dropping a patient's message.
    """
    if not event_id:
        return False
    from tools.database import get_pool

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            inserted = await conn.fetchval(
                "INSERT INTO inbound_events (provider, event_id) VALUES ($1, $2) "
                "ON CONFLICT DO NOTHING RETURNING 1", provider, event_id,
            )
        return inserted is None
    except Exception:
        log.warning("inbound de-dup unavailable for %s", provider, exc_info=True)
        return False
