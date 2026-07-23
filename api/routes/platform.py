"""Platform-admin dashboard — cross-tenant revenue, hospital billing, and the
payment ledger. Every endpoint is gated to the `platform_admin` JWT role (the
only role that legitimately sees across all hospitals).

The platform admin operates the business here: watch booking-fee + subscription
revenue, mark a hospital's monthly subscription paid (reactivating a suspended
hospital), manually confirm a held booking a patient paid out-of-band, and flag
a payment for refund.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from tools.database import (
    confirm_paid_booking,
    get_payment,
    list_hospitals_admin,
    list_payments,
    mark_subscription_invoice_paid,
    platform_revenue_stats,
    refund_payment,
)

from ..deps import require_role
from ..schemas import (
    PlatformHospitalOut,
    PlatformOverviewOut,
    PlatformPaymentOut,
)

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/platform",
    tags=["platform-admin"],
    dependencies=[Depends(require_role("platform_admin"))],
)


@router.get("/overview", response_model=PlatformOverviewOut)
async def overview() -> PlatformOverviewOut:
    stats = await platform_revenue_stats()
    hospitals = await list_hospitals_admin()
    return PlatformOverviewOut(**stats, hospitals=hospitals)


@router.get("/hospitals", response_model=list[PlatformHospitalOut])
async def hospitals() -> list[dict]:
    return await list_hospitals_admin()


@router.post("/hospitals/{hospital_id}/subscription/mark-paid")
async def mark_hospital_paid(hospital_id: int) -> dict:
    """Record a hospital's monthly subscription payment: advances the billing
    period by a month and reactivates the hospital if it had lapsed."""
    try:
        result = await mark_subscription_invoice_paid(hospital_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"ok": True, **{k: dict(v) for k, v in result.items()}}


@router.get("/payments", response_model=list[PlatformPaymentOut])
async def payments(
    kind: str | None = Query(None),
    status: str | None = Query(None),
    hospital_id: int | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
) -> list[dict]:
    return await list_payments(kind=kind, status=status, hospital_id=hospital_id, limit=limit)


@router.post("/payments/{payment_id}/mark-paid")
async def mark_payment_paid(payment_id: str, request: Request) -> dict:
    """Manually confirm a payment the patient settled out-of-band (e.g. the
    manual provider's pay-at-desk flow, or a bKash transfer). Reuses the same
    idempotent confirmation path as the gateway IPN, including the held-booking
    promotion + confirmation SMS."""
    payment = await get_payment(payment_id)
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    outcome = await confirm_paid_booking(payment_id, val_id="manual-admin", raw={"manual_admin": True})
    if outcome["status"] not in ("ok", "already_paid"):
        raise HTTPException(status_code=409, detail=f"Cannot confirm: {outcome['status']}")

    # Booking-fee confirmation fires the same SMS/notify/memory the IPN path does.
    if outcome["status"] == "ok" and outcome.get("appointment") and payment["kind"] == "booking_fee":
        from .patient_portal import _notify_and_remember

        appt = outcome["appointment"]
        await _notify_and_remember(
            graph=request.app.state.graph, account_id=payment["account_id"],
            patient_name=appt["patient_name"], patient_mobile=appt["patient_mobile"],
            slot_label=appt["slot_label"], clinic_id=appt["clinic_id"],
            doctor_id=appt["doctor_id"], serial_number=appt["serial_number"],
            patient_age=appt["patient_age"], appointment_id=outcome["appointment_id"],
        )
    return {"ok": True, "status": outcome["status"]}


@router.post("/payments/{payment_id}/refund")
async def refund(payment_id: str, note: str = Query("")) -> dict:
    """Flag a paid payment as refunded (records intent + note; the actual
    bKash/Nagad refund is done by hand for the pilot)."""
    row = await refund_payment(payment_id, note=note)
    if row is None:
        raise HTTPException(status_code=409, detail="Payment is not in a refundable (paid) state")
    return {"ok": True}
