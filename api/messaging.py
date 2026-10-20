"""Inbound text-message routing (WhatsApp + SMS) — platform mode by default.

ASA has ONE WhatsApp number and ONE SMS number for the whole marketplace, like
the platform voice line (docs/TELEPHONY.md). For each inbound message:

  * a business number MAPPED in `channels` (a hospital's own WhatsApp/SMS
    line) keeps that clinic's scope — the optional per-hospital setup;
  * any other business number (the ASA number) runs the agent in PLATFORM
    mode — search every hospital, book anywhere;
  * a sender whose phone is VERIFIED on a patient account (one-time OTP, see
    /portal/account) joins that account's unified thread
    `pt-acc{id}-platform` — the same conversation as their portal chat and
    voice calls, with bookings linked to the account and cancel/reschedule
    available. Anyone else gets an anonymous platform thread keyed on their
    number.

Turns for one sender are serialised (sender_lock) so two quick messages can't
race on the same LangGraph checkpoint.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import weakref
from contextlib import asynccontextmanager
from typing import AsyncIterator

from config import settings
from tools.database import (
    get_clinic_id_by_channel,
    get_default_clinic_id,
    get_pool,
    get_verified_account_by_phone,
)
from utils.text import normalize_bd_mobile

from .routes.patient_portal import _platform_session_id

log = logging.getLogger(__name__)


async def resolve_message_scope(kind: str, business_number: str, sender: str) -> dict:
    """Decide thread + scope for one inbound message.

    kind: 'whatsapp' | 'sms'. Returns the session_id plus the keyword
    arguments for agent.runner.run_message_turn.
    """
    sender_session = f"{kind}-{sender}"
    clinic_id = await get_clinic_id_by_channel(kind, business_number) if business_number else None
    if clinic_id is not None:
        return {"session_id": sender_session, "turn": {"clinic_id": clinic_id, "channel": kind}}

    if not settings.messaging_platform_mode:
        # Legacy single-clinic fallback (the pre-marketplace behaviour).
        return {
            "session_id": sender_session,
            "turn": {"clinic_id": await get_default_clinic_id(), "channel": kind},
        }

    phone = normalize_bd_mobile(sender)
    account = None
    if len(phone) == 11:
        try:
            account = await get_verified_account_by_phone(phone)
        except Exception:
            # Identity lookup failing must not drop the message — answer
            # anonymously rather than not at all.
            log.warning("verified-account lookup failed for %s message", kind, exc_info=True)
    if account:
        return {
            "session_id": _platform_session_id(account["id"]),
            "turn": {
                "channel": kind, "platform": True,
                "patient_account_id": account["id"],
                "patient_name": account.get("name") or None,
                "patient_mobile": account["phone"],
            },
            "account_id": account["id"],
        }
    return {
        "session_id": sender_session,
        "turn": {"channel": kind, "platform": True, "patient_mobile": phone if len(phone) == 11 else None},
    }


# Weak values: a sender's lock lives exactly as long as some task holds or
# awaits it, so the map never grows with every number that ever messaged us.
_local_locks: "weakref.WeakValueDictionary[str, asyncio.Lock]" = weakref.WeakValueDictionary()


def _lock_key(session_id: str) -> int:
    # Stable signed 64-bit key for pg_advisory_lock.
    return int.from_bytes(hashlib.sha256(session_id.encode()).digest()[:8], "big", signed=True)


@asynccontextmanager
async def sender_lock(session_id: str) -> AsyncIterator[None]:
    """Serialise turns on one thread across tasks AND uvicorn workers.

    In-process lock first (cheap, same worker), then a Postgres session
    advisory lock so a message handled by another worker waits too. If the
    DB lock can't be taken (DB down) the in-process lock alone still applies.
    """
    local = _local_locks.get(session_id)
    if local is None:
        local = asyncio.Lock()
        _local_locks[session_id] = local
    async with local:
        conn = None
        pool = None
        try:
            pool = await get_pool()
            conn = await pool.acquire()
            await conn.execute("SELECT pg_advisory_lock($1)", _lock_key(session_id))
        except Exception:
            log.warning("advisory lock unavailable for %s", session_id, exc_info=True)
            if conn is not None and pool is not None:
                await pool.release(conn)
            conn = None
        try:
            yield
        finally:
            if conn is not None:
                try:
                    await conn.execute("SELECT pg_advisory_unlock($1)", _lock_key(session_id))
                finally:
                    await pool.release(conn)
