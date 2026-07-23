"""Patient-review moderation (per clinic).

GET   /reviews?status=  -> every review of this clinic's doctors (optionally
                           filtered to published|hidden)
PATCH /reviews/{id}     -> hide or re-publish one review

Reviews are written by patients through the portal (PUT
/patient/doctors/{id}/review); hospital staff can only change visibility.
Tenancy is enforced in the UPDATE itself — a review of another clinic's
doctor 404s.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from tools.database import list_reviews_for_clinic, set_review_status

from ..deps import audit_action, current_clinic_id, current_user
from ..schemas import AdminReviewOut, ReviewStatusUpdate

router = APIRouter(prefix="/reviews", tags=["reviews"])


@router.get("", response_model=list[AdminReviewOut])
async def get_reviews(
    status: str | None = None, clinic_id: int = Depends(current_clinic_id)
):
    if status not in (None, "published", "hidden"):
        raise HTTPException(status_code=400, detail="status must be published or hidden")
    return await list_reviews_for_clinic(clinic_id, status)


@router.patch("/{review_id}", status_code=204)
async def moderate_review(
    review_id: int, body: ReviewStatusUpdate, request: Request,
    clinic_id: int = Depends(current_clinic_id),
    user: dict = Depends(current_user),
):
    ok = await set_review_status(clinic_id, review_id, body.status)
    if not ok:
        raise HTTPException(status_code=404, detail="Review not found")
    await audit_action(
        user, request, action="moderate_review", entity_type="review",
        entity_id=review_id, clinic_id=clinic_id, new_value={"status": body.status},
    )
