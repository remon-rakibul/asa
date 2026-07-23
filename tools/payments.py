"""Payment provider abstraction — booking fees + patient subscriptions.

Two providers behind the same interface (PAYMENT_PROVIDER env var):

  * "manual" — no gateway account needed. Either auto-confirms immediately
    (PAYMENT_MANUAL_AUTOPAY=true, the default — good for local dev and a
    fee-free pilot) or hands back a portal page with bKash/Nagad-to-number
    instructions that a platform admin marks paid by hand
    (POST /platform/payments/{id}/mark-paid).
  * "sslcommerz" — a free-sandbox Bangladeshi gateway aggregating
    bKash/Nagad/cards. Classic (non-embedded) checkout: initiate() posts to
    the session API and returns a GatewayPageURL to redirect the patient to;
    SSLCommerz POSTs an IPN back with a val_id, which validate() ALWAYS
    re-checks server-side against the validator API (never trust the IPN
    payload alone — it's replayable).

Callers only see initiate()/validate(); which provider runs is invisible.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional, Protocol

import httpx

from config import settings

log = logging.getLogger(__name__)


class PaymentProvider(Protocol):
    async def initiate(
        self, *, payment_id: str, amount: int, currency: str,
        success_url: str, fail_url: str, cancel_url: str, ipn_url: str,
        customer_name: str, customer_phone: str,
    ) -> dict:
        """Start a payment. Returns {"auto_paid": True} (manual autopay —
        caller should confirm immediately) or {"pay_url": str} to redirect
        the patient to."""
        ...

    async def validate(self, payload: dict) -> dict:
        """Server-side re-check of an inbound IPN/callback payload. Returns
        {"ok": bool, "provider_ref": str, "val_id": str, "amount": float, "raw": dict}."""
        ...


class ManualProvider:
    """No real gateway. Autopay short-circuits confirmation; otherwise the
    portal shows collection instructions and a staff member confirms it."""

    async def initiate(self, *, payment_id: str, amount: int, **_kwargs) -> dict:
        if settings.payment_manual_autopay:
            return {"auto_paid": True}
        return {"pay_url": f"{settings.portal_base_url}/portal/pay/{payment_id}"}

    async def validate(self, payload: dict) -> dict:
        # Only reached via the platform-admin "mark paid" action, which
        # already knows which payment it's confirming — treat as valid.
        return {"ok": True, "provider_ref": payload.get("provider_ref", ""),
                "val_id": "", "amount": payload.get("amount", 0), "raw": payload}


class SSLCommerzProvider:
    """Classic (non-embedded) SSLCommerz checkout. Field names per the
    SSLCommerz v4 API: https://developer.sslcommerz.com/doc/v4/."""

    def _base_url(self) -> str:
        host = "sandbox.sslcommerz.com" if settings.sslcommerz_sandbox else "securepay.sslcommerz.com"
        return f"https://{host}"

    async def initiate(
        self, *, payment_id: str, amount: int, currency: str,
        success_url: str, fail_url: str, cancel_url: str, ipn_url: str,
        customer_name: str, customer_phone: str,
    ) -> dict:
        # SSLCommerz requires total_amount >= 10.00 BDT.
        body = {
            "store_id": settings.sslcommerz_store_id,
            "store_passwd": settings.sslcommerz_store_passwd,
            "total_amount": f"{max(amount, 10):.2f}",
            "currency": currency,
            "tran_id": payment_id,
            "success_url": success_url,
            "fail_url": fail_url,
            "cancel_url": cancel_url,
            "ipn_url": ipn_url,
            "cus_name": customer_name or "Patient",
            "cus_email": "patient@example.com",  # SSLCommerz requires the field; not used for delivery
            "cus_add1": "Bangladesh",
            "cus_city": "Dhaka",
            "cus_postcode": "1000",
            "cus_country": "Bangladesh",
            "cus_phone": customer_phone or "01700000000",
            "shipping_method": "NO",
            "product_name": "Appointment booking fee",
            "product_category": "service",
            "product_profile": "general",
        }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(f"{self._base_url()}/gwprocess/v4/api.php", data=body)
            resp.raise_for_status()
            data = resp.json()
        if data.get("status") != "SUCCESS" or not data.get("GatewayPageURL"):
            log.warning("SSLCommerz initiate failed: %s", data.get("failedreason"))
            raise RuntimeError(data.get("failedreason") or "Payment gateway did not return a checkout URL")
        return {"pay_url": data["GatewayPageURL"]}

    async def validate(self, payload: dict) -> dict:
        val_id = payload.get("val_id", "")
        if not val_id:
            return {"ok": False, "provider_ref": payload.get("tran_id", ""), "val_id": "", "amount": 0, "raw": payload}
        params = {
            "val_id": val_id,
            "store_id": settings.sslcommerz_store_id,
            "store_passwd": settings.sslcommerz_store_passwd,
            "format": "json",
        }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{self._base_url()}/validator/api/validationserverAPI.php", params=params,
            )
            resp.raise_for_status()
            data = resp.json()
        ok = data.get("status") in ("VALID", "VALIDATED")
        return {
            "ok": ok,
            "provider_ref": data.get("tran_id", payload.get("tran_id", "")),
            "val_id": val_id,
            "amount": float(data.get("amount") or 0),
            "raw": data,
        }


_PROVIDERS: dict[str, PaymentProvider] = {
    "manual": ManualProvider(),
    "sslcommerz": SSLCommerzProvider(),
}


def get_provider() -> PaymentProvider:
    return _PROVIDERS.get((settings.payment_provider or "manual").lower(), _PROVIDERS["manual"])


def new_provider_ref() -> str:
    """A tran_id safe for every provider (SSLCommerz caps it at 30 chars)."""
    return uuid.uuid4().hex[:30]
