"""Hospital-facing prepaid credit wallet — balance, ledger, and buy-credits.

A hospital_admin sees and tops up only their own hospital's wallet. Credits are
priced at the hospital's negotiated ৳/credit rate; a top-up creates a
`credit_topup` payment and, once it confirms (gateway IPN or manual autopay),
`confirm_paid_booking` loads the wallet. Superadmin controls (rate, grants) live
in the platform-admin surface, not here.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from config import settings
from tools.database import (
    confirm_paid_booking,
    create_payment,
    get_hospital,
    get_or_create_hospital_wallet,
    list_wallet_ledger,
)
from tools.payments import get_provider, new_provider_ref

from ..deps import current_user
from ..schemas import (
    PaymentPromptOut,
    WalletLedgerEntry,
    WalletOut,
    WalletTopupIn,
    WalletTopupOut,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/hospital", tags=["hospital-wallet"])


async def _caller_hospital_id(user: dict = Depends(current_user)) -> int:
    """A hospital_admin scoped to exactly one hospital. Rejects platform admins
    (they use the superadmin wallet controls) and unscoped users."""
    if user.get("role") != "hospital_admin" or not user.get("hospital_id"):
        raise HTTPException(status_code=403, detail="Hospital admin account required")
    return int(user["hospital_id"])


def _wallet_out(wallet: dict, ledger: list[dict]) -> WalletOut:
    return WalletOut(
        hospital_id=wallet["hospital_id"],
        balance=wallet["balance"],
        credit_rate_bdt=float(wallet["credit_rate_bdt"]),
        low_balance=wallet["balance"] < settings.wallet_low_balance_credits,
        ledger=[WalletLedgerEntry(**e) for e in ledger],
    )


@router.get("/wallet", response_model=WalletOut)
async def get_wallet(hospital_id: int = Depends(_caller_hospital_id)) -> WalletOut:
    wallet = await get_or_create_hospital_wallet(hospital_id)
    ledger = await list_wallet_ledger(hospital_id, limit=50)
    return _wallet_out(wallet, ledger)


@router.post("/wallet/topup", response_model=WalletTopupOut)
async def topup_wallet(
    body: WalletTopupIn, hospital_id: int = Depends(_caller_hospital_id)
) -> WalletTopupOut:
    """Buy `credits`. Prices them at the hospital's rate, creates a credit_topup
    payment, and returns a pay prompt; the wallet is loaded when payment
    confirms — immediately for the manual provider's autopay, else via IPN."""
    wallet = await get_or_create_hospital_wallet(hospital_id)
    rate = float(wallet["credit_rate_bdt"])
    amount = round(body.credits * rate)
    hospital = await get_hospital(hospital_id)
    provider_ref = new_provider_ref()
    payment = await create_payment(
        kind="credit_topup", amount=amount, provider=settings.payment_provider,
        provider_ref=provider_ref, hospital_id=hospital_id, credits=body.credits,
    )
    try:
        init = await get_provider().initiate(
            payment_id=provider_ref, amount=amount, currency="BDT",
            success_url=f"{settings.public_base_url}/payments/redirect/success",
            fail_url=f"{settings.public_base_url}/payments/redirect/fail",
            cancel_url=f"{settings.public_base_url}/payments/redirect/cancel",
            ipn_url=f"{settings.public_base_url}/payments/ipn/{settings.payment_provider}",
            customer_name=(hospital or {}).get("name") or "Hospital",
            customer_phone="",
        )
    except Exception:
        log.warning("wallet top-up initiate failed for hospital %s", hospital_id, exc_info=True)
        raise HTTPException(status_code=502, detail="Payment gateway is unavailable, try again shortly")

    if init.get("auto_paid"):
        outcome = await confirm_paid_booking(payment["id"], val_id="", raw={"auto_paid": True})
        if outcome["status"] in ("ok", "already_paid"):
            fresh = await get_or_create_hospital_wallet(hospital_id)
            return WalletTopupOut(
                balance=fresh["balance"], credit_rate_bdt=rate,
                payment=PaymentPromptOut(payment_id=payment["id"], amount=amount,
                                         currency="BDT", pay_url=None, expires_at=None),
            )

    return WalletTopupOut(
        balance=wallet["balance"], credit_rate_bdt=rate,
        payment=PaymentPromptOut(payment_id=payment["id"], amount=amount,
                                 currency="BDT", pay_url=init.get("pay_url"), expires_at=None),
    )
