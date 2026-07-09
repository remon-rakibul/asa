"""Reporting / analytics endpoints for hospital staff.

GET /reports/summary?date_from&date_to -> appointment + SMS + channel aggregates
for the caller's clinic over a date range (defaults to the last 30 days).
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query

from tools.database import appointment_stats, get_channel_stats, sms_stats

from ..deps import current_clinic_id, require_role
from ..schemas import ReportSummaryOut

router = APIRouter(prefix="/reports", tags=["reports"])


def _validate_range(date_from: str | None, date_to: str | None) -> tuple[str, str]:
    """Default to the last 30 days; validate ISO dates and ordering."""
    today = date.today()
    df = date_from or (today - timedelta(days=29)).isoformat()
    dt = date_to or today.isoformat()
    try:
        d_from = date.fromisoformat(df)
        d_to = date.fromisoformat(dt)
    except ValueError:
        raise HTTPException(status_code=400, detail="Dates must be YYYY-MM-DD")
    if d_from > d_to:
        raise HTTPException(status_code=400, detail="date_from must be on or before date_to")
    return df, dt


@router.get("/summary", response_model=ReportSummaryOut)
async def summary(
    date_from: str | None = Query(None, description="YYYY-MM-DD (inclusive)"),
    date_to: str | None = Query(None, description="YYYY-MM-DD (inclusive)"),
    _: dict = Depends(require_role("hospital_admin", "dept_head", "platform_admin")),
    clinic_id: int = Depends(current_clinic_id),
) -> dict:
    df, dt = _validate_range(date_from, date_to)
    return {
        "date_from": df,
        "date_to": dt,
        "appointments": await appointment_stats(clinic_id, df, dt),
        "sms": await sms_stats(clinic_id, df, dt),
        "channels": await get_channel_stats(clinic_id),
    }
