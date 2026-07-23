"""Doctor roster endpoints (per clinic).

GET    /doctors            -> list this clinic's doctors (primary first)
POST   /doctors            -> add a doctor
PATCH  /doctors/{id}       -> edit a doctor (name/specialty/degrees/description/phone/primary)
DELETE /doctors/{id}       -> remove a doctor
PUT    /doctors/{id}/photo -> upload/replace the profile photo (jpeg/png/webp, max 2 MB)
DELETE /doctors/{id}/photo -> remove the profile photo
GET    /doctors/{id}/photo -> serve the photo (public: <img src> can't send the
                              Bearer header, and a doctor's photo is public
                              directory info shown to any portal visitor)

The primary doctor's name + phone mirror into the clinic row used by the agent
and notification SMS, so changes here take effect on the next turn.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, UploadFile

from agent.nodes import invalidate_clinic
from tools.database import (
    add_doctor,
    delete_doctor,
    get_doctor_photo,
    list_doctors,
    set_doctor_photo,
    update_doctor,
)

from ..deps import audit_action, current_clinic_id, current_user
from ..schemas import DoctorCreate, DoctorOut, DoctorUpdate

router = APIRouter(prefix="/doctors", tags=["doctors"])

_PHOTO_MAX_BYTES = 2 * 1024 * 1024
_PHOTO_TYPES = {"image/jpeg", "image/png", "image/webp"}


@router.get("", response_model=list[DoctorOut])
async def get_doctors(clinic_id: int = Depends(current_clinic_id)):
    return await list_doctors(clinic_id)


@router.post("", response_model=DoctorOut, status_code=201)
async def create_doctor(
    body: DoctorCreate, request: Request,
    clinic_id: int = Depends(current_clinic_id),
    user: dict = Depends(current_user),
):
    row = await add_doctor(
        clinic_id=clinic_id,
        name=body.name.strip(),
        specialty=body.specialty.strip(),
        degrees=body.degrees.strip(),
        description=body.description.strip(),
        phone=body.phone.strip(),
        is_primary=body.is_primary,
        fee_new=body.fee_new,
        fee_followup=body.fee_followup,
    )
    invalidate_clinic(clinic_id)
    await audit_action(
        user, request, action="create_doctor", entity_type="doctor",
        entity_id=row.get("id"), clinic_id=clinic_id,
        new_value={"name": body.name.strip(), "specialty": body.specialty.strip()},
    )
    return row


@router.patch("/{doctor_id}", response_model=DoctorOut)
async def patch_doctor(
    doctor_id: int, body: DoctorUpdate, request: Request,
    clinic_id: int = Depends(current_clinic_id),
    user: dict = Depends(current_user),
):
    patch = body.model_dump(exclude_unset=True)
    row = await update_doctor(clinic_id, doctor_id, **patch)
    if row is None:
        raise HTTPException(status_code=404, detail="Doctor not found")
    invalidate_clinic(clinic_id)
    await audit_action(
        user, request, action="update_doctor", entity_type="doctor",
        entity_id=doctor_id, clinic_id=clinic_id, new_value=patch,
    )
    return row


@router.delete("/{doctor_id}", status_code=204)
async def remove_doctor(
    doctor_id: int, request: Request,
    clinic_id: int = Depends(current_clinic_id),
    user: dict = Depends(current_user),
):
    ok = await delete_doctor(clinic_id, doctor_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Doctor not found")
    invalidate_clinic(clinic_id)
    await audit_action(
        user, request, action="delete_doctor", entity_type="doctor",
        entity_id=doctor_id, clinic_id=clinic_id,
    )


@router.put("/{doctor_id}/photo", status_code=204)
async def upload_doctor_photo(
    doctor_id: int, file: UploadFile, request: Request,
    clinic_id: int = Depends(current_clinic_id),
    user: dict = Depends(current_user),
):
    content_type = (file.content_type or "").lower()
    if content_type not in _PHOTO_TYPES:
        raise HTTPException(
            status_code=415, detail="Unsupported image type. Upload JPEG, PNG, or WebP."
        )
    raw = await file.read()
    if len(raw) > _PHOTO_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Photo too large (max 2 MB).")
    if not raw:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    ok = await set_doctor_photo(clinic_id, doctor_id, raw, content_type)
    if not ok:
        raise HTTPException(status_code=404, detail="Doctor not found")
    await audit_action(
        user, request, action="update_doctor_photo", entity_type="doctor",
        entity_id=doctor_id, clinic_id=clinic_id,
        new_value={"content_type": content_type, "bytes": len(raw)},
    )


@router.delete("/{doctor_id}/photo", status_code=204)
async def remove_doctor_photo(
    doctor_id: int, request: Request,
    clinic_id: int = Depends(current_clinic_id),
    user: dict = Depends(current_user),
):
    ok = await set_doctor_photo(clinic_id, doctor_id, None, None)
    if not ok:
        raise HTTPException(status_code=404, detail="Doctor not found")
    await audit_action(
        user, request, action="delete_doctor_photo", entity_type="doctor",
        entity_id=doctor_id, clinic_id=clinic_id,
    )


@router.get("/{doctor_id}/photo")
async def serve_doctor_photo(doctor_id: int) -> Response:
    photo = await get_doctor_photo(doctor_id)
    if photo is None:
        raise HTTPException(status_code=404, detail="No photo")
    data, mime = photo
    return Response(
        content=data, media_type=mime,
        # The UI cache-busts with a query param after upload, so a short
        # shared cache is safe and keeps repeat tile renders cheap.
        headers={"Cache-Control": "public, max-age=300"},
    )
