"""Unauthenticated, public-facing endpoints for the marketing landing page.

No JWT, no tenant scope — anything here is world-readable and must expose only
non-sensitive, non-personal data. Today that is the platform's published pricing,
read live from settings so the landing page can never drift from config.py.
"""

from __future__ import annotations

from fastapi import APIRouter

from config import settings

from ..schemas import PublicPricingOut

router = APIRouter(prefix="/public", tags=["public"])


@router.get("/pricing", response_model=PublicPricingOut)
async def pricing() -> PublicPricingOut:
    """Published plan pricing for the landing page. Single-sourced from settings
    so changing a fee in config updates the marketing page with no code edit."""
    return PublicPricingOut(
        patient_subscription_fee=settings.patient_subscription_fee,
        hospital_subscription_fee=settings.hospital_subscription_fee,
        free_agent_bookings_per_month=settings.free_agent_bookings_per_month,
        patient_trial_days=settings.patient_trial_days,
        currency="BDT",
        credits_enabled=settings.credits_enabled,
        default_credit_rate_bdt=settings.default_credit_rate_bdt,
    )
