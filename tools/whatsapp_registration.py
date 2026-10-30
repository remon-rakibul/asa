"""Keep the WhatsApp number registered with the Cloud API.

Symptom: WhatsApp stops working in both directions and sends fail with
`(#133010) Account not registered` — Meta shows the number's status as
PENDING instead of CONNECTED. Meta test numbers drop back to PENDING from
time to time; registering again with the same two-step PIN restores them.

Used by:
  * the backend's registration watchdog (api/app.py) — checks at startup and
    every WHATSAPP_REGISTRATION_CHECK_MINUTES, re-registers only when needed;
  * `make whatsapp-register` (scripts/whatsapp_register.py) — on demand.

The check is one cheap GET; /register is only called when the number is not
CONNECTED. Needs WHATSAPP_TOKEN, WHATSAPP_PHONE_ID, WHATSAPP_REGISTRATION_PIN.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import httpx

from config import settings

log = logging.getLogger(__name__)

_GRAPH = "https://graph.facebook.com/v25.0"
# Registration can take well over httpx's default 5 s.
_TIMEOUT = 90.0


@dataclass
class RegistrationResult:
    before: str | None          # status seen first (None = couldn't check)
    after: str | None           # status after any registration attempt
    attempted: bool = False     # /register was called
    error: str | None = None    # why it couldn't run / failed

    @property
    def ok(self) -> bool:
        return self.after == "CONNECTED"


def pin_problem() -> str | None:
    """Why registration can't run, or None if it can."""
    if not (settings.whatsapp_token and settings.whatsapp_phone_id):
        return "WHATSAPP_TOKEN / WHATSAPP_PHONE_ID are not set"
    if not re.fullmatch(r"\d{6}", (settings.whatsapp_registration_pin or "").strip()):
        return "WHATSAPP_REGISTRATION_PIN must be 6 digits"
    return None


async def _status(client: httpx.AsyncClient) -> str:
    r = await client.get(f"{_GRAPH}/{settings.whatsapp_phone_id}", params={"fields": "status"})
    r.raise_for_status()
    return (r.json() or {}).get("status", "UNKNOWN")


async def ensure_registered(force: bool = False) -> RegistrationResult:
    """Register the number if it isn't CONNECTED (or always, with force). Never raises."""
    problem = pin_problem()
    if problem:
        return RegistrationResult(None, None, error=problem)

    headers = {"Authorization": f"Bearer {settings.whatsapp_token}"}
    try:
        async with httpx.AsyncClient(headers=headers, timeout=_TIMEOUT) as client:
            before = await _status(client)
            if before == "CONNECTED" and not force:
                return RegistrationResult(before, before)
            error = None
            try:
                r = await client.post(
                    f"{_GRAPH}/{settings.whatsapp_phone_id}/register",
                    json={"messaging_product": "whatsapp",
                          "pin": settings.whatsapp_registration_pin.strip()},
                )
                if r.status_code >= 400:
                    body = r.json() if r.content else {}
                    error = f"HTTP {r.status_code}: {(body.get('error') or {}).get('message', '')[:200]}"
            except httpx.TimeoutException:
                # Meta sometimes finishes after the client gives up — re-check below.
                error = "register request timed out"
            after = await _status(client)
            return RegistrationResult(before, after, attempted=True,
                                      error=None if after == "CONNECTED" else error)
    except Exception as exc:
        # Network/DNS hiccup or bad token — report, never crash the caller.
        detail = f"HTTP {exc.response.status_code}" if isinstance(exc, httpx.HTTPStatusError) \
            else type(exc).__name__
        return RegistrationResult(None, None, error=detail)
