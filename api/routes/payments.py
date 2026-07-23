"""Payment gateway callbacks — public, no patient auth (the gateway calls
these directly, not the patient's browser with a Bearer token).

POST/GET /payments/ipn/sslcommerz    -> server-to-server payment notification
GET/POST /payments/redirect/{outcome} -> browser bounce-back after checkout
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from config import settings
from tools.database import confirm_paid_booking, get_payment_by_provider_ref
from tools.payments import get_provider

log = logging.getLogger(__name__)

router = APIRouter(prefix="/payments", tags=["payments"])


async def _process_ipn(payload: dict, *, graph) -> dict:
    """Shared body for the IPN handler — always re-validates server-side
    against the gateway's validator API (never trusts the posted payload
    alone, which is trivially replayable/forgeable) and checks the amount
    matches what we actually charged before confirming anything."""
    provider = get_provider()
    result = await provider.validate(payload)
    if not result["ok"]:
        log.warning("payment validation failed: ref=%s", result.get("provider_ref"))
        return {"ok": False}

    payment = await get_payment_by_provider_ref(settings.payment_provider, result["provider_ref"])
    if not payment:
        log.warning("IPN for unknown payment ref=%s", result["provider_ref"])
        return {"ok": False}

    # Reject only UNDERpayment (patient tampering the checkout to pay less than
    # the fee). Overpayment is benign and expected: SSLCommerz enforces a ৳10
    # minimum, so a sub-৳10 fee is legitimately charged at ৳10 — an exact-match
    # check would wrongly reject those and strand a paid booking as unconfirmed.
    if result["amount"] and float(result["amount"]) + 0.5 < payment["amount"]:
        log.warning(
            "payment underpaid: expected %s got %s (payment %s)",
            payment["amount"], result["amount"], payment["id"],
        )
        return {"ok": False}

    outcome = await confirm_paid_booking(payment["id"], val_id=result["val_id"], raw=result["raw"])
    if outcome["status"] == "ok" and outcome["appointment"] and payment["kind"] == "booking_fee":
        # Booking-fee payment just confirmed a held appointment — this is the
        # first moment it's real, so the confirmation SMS/notification/memory
        # write (skipped at booking time for a gateway payment) happens now.
        from .patient_portal import _notify_and_remember

        appt = outcome["appointment"]
        await _notify_and_remember(
            graph=graph, account_id=payment["account_id"], patient_name=appt["patient_name"],
            patient_mobile=appt["patient_mobile"], slot_label=appt["slot_label"],
            clinic_id=appt["clinic_id"], doctor_id=appt["doctor_id"],
            serial_number=appt["serial_number"], patient_age=appt["patient_age"],
            appointment_id=outcome["appointment_id"],
        )
    return {"ok": outcome["status"] in ("ok", "already_paid")}


@router.api_route("/ipn/sslcommerz", methods=["GET", "POST"])
async def sslcommerz_ipn(request: Request) -> dict:
    # SSLCommerz POSTs form-encoded fields; tolerate a GET with query params too.
    if request.method == "POST":
        payload = dict(await request.form())
    else:
        payload = dict(request.query_params)
    # Always 200 with an {"ok": bool} body — the gateway retries the IPN on
    # anything but a 2xx, and confirm_paid_booking is idempotent, so a
    # transient failure here just gets a free retry rather than a dead letter.
    return await _process_ipn(payload, graph=request.app.state.graph)


@router.api_route("/redirect/{outcome}", methods=["GET", "POST"])
async def payment_redirect(outcome: str, request: Request) -> RedirectResponse:
    """The browser lands here after checkout (success/fail/cancel) — bounce
    it to the portal's result page. The actual confirmation always comes
    from the IPN above, not this redirect, so a patient closing the tab
    early can't skip payment."""
    if outcome not in ("success", "fail", "cancel"):
        raise HTTPException(status_code=404)
    if request.method == "POST":
        form = await request.form()
        tran_id = form.get("tran_id")
    else:
        tran_id = request.query_params.get("tran_id")
    url = f"{settings.portal_base_url}/portal/pay/result?outcome={outcome}"
    if tran_id:
        url += f"&payment_id={tran_id}"
    return RedirectResponse(url, status_code=303)
