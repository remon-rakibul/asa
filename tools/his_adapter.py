"""HIS/EMR integration adapter.

The agent is always standalone-capable.  When a hospital configures a
his_webhook_url on their record, appointment events are pushed to that URL
via exponential-backoff retries.  Failures are logged but never block booking.

Inbound HIS sync is handled by api/routes/his.py (POST /his/sync).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

import httpx

log = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3
_BACKOFF_BASE = 2.0  # seconds; doubles each retry


async def _push(url: str, api_key: str, payload: dict[str, Any]) -> None:
    """POST payload to the HIS webhook with retries. Fire-and-forget."""
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=10.0) as client:
        for attempt in range(_MAX_ATTEMPTS):
            try:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                log.debug("HIS push succeeded (attempt %d): %s", attempt + 1, url)
                return
            except Exception as exc:
                wait = _BACKOFF_BASE ** attempt
                log.warning(
                    "HIS push failed (attempt %d/%d, retry in %.0fs): %s",
                    attempt + 1, _MAX_ATTEMPTS, wait, exc,
                )
                if attempt < _MAX_ATTEMPTS - 1:
                    await asyncio.sleep(wait)
    log.error("HIS push gave up after %d attempts: %s", _MAX_ATTEMPTS, url)


async def notify_appointment_booked(
    hospital: dict, appointment: dict
) -> None:
    """Push a new booking to the hospital's HIS (no-op if not configured)."""
    url = (hospital or {}).get("his_webhook_url", "")
    api_key = (hospital or {}).get("his_api_key", "")
    if not url:
        return
    await _push(url, api_key, {"event": "appointment_booked", "appointment": appointment})


async def notify_appointment_cancelled(
    hospital: dict, appointment: dict
) -> None:
    """Push a cancellation to the hospital's HIS (no-op if not configured)."""
    url = (hospital or {}).get("his_webhook_url", "")
    api_key = (hospital or {}).get("his_api_key", "")
    if not url:
        return
    await _push(url, api_key, {"event": "appointment_cancelled", "appointment": appointment})


async def notify_patient_registered(
    hospital: dict, patient: dict
) -> None:
    """Push a new patient registration to the HIS (no-op if not configured)."""
    url = (hospital or {}).get("his_webhook_url", "")
    api_key = (hospital or {}).get("his_api_key", "")
    if not url:
        return
    await _push(url, api_key, {"event": "patient_registered", "patient": patient})


async def lookup_patient_in_his(
    hospital: dict, mobile: str
) -> Optional[dict]:
    """Query the HIS for a patient by mobile (returns None if not found or no HIS)."""
    url = (hospital or {}).get("his_patient_lookup_url", "")
    api_key = (hospital or {}).get("his_api_key", "")
    if not url:
        return None
    try:
        headers = {"Authorization": f"Bearer {api_key}"}
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                url, params={"mobile": mobile}, headers=headers
            )
            if resp.status_code == 200:
                return resp.json()
    except Exception as exc:
        log.warning("HIS patient lookup failed: %s", exc)
    return None
