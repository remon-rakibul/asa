"""Patient registry endpoints (hospital mode)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from api.deps import audit_action, current_hospital_id, current_user, require_role
from api.schemas import PatientCreate, PatientOut
from tools.database import (
    create_patient,
    get_patient_by_mrn,
    get_patient_by_phone,
    list_patients,
)

router = APIRouter(prefix="/patients", tags=["patients"])


def _resolve_hospital(jwt_hospital_id: int | None, query_hospital_id: int | None, role: str) -> int:
    """platform_admin may pass ?hospital_id=; other roles use their JWT scope."""
    hid = query_hospital_id if role == "platform_admin" else jwt_hospital_id
    if not hid:
        raise HTTPException(
            status_code=422,
            detail="Patient endpoints require a hospital-scoped account. "
                   "Platform admins must supply ?hospital_id=<id>.",
        )
    return hid


@router.get("", response_model=list[PatientOut])
async def search_patients(
    q: str | None = Query(None, description="Search by name, phone, or MRN"),
    limit: int = Query(50, ge=1, le=500),
    hospital_id: int | None = Query(None, description="Required for platform_admin"),
    user: dict = Depends(require_role("hospital_admin", "dept_head", "receptionist", "platform_admin")),
    jwt_hospital_id: int | None = Depends(current_hospital_id),
) -> list[dict]:
    hid = _resolve_hospital(jwt_hospital_id, hospital_id, user["role"])
    return await list_patients(hid, q=q, limit=limit)


@router.get("/{mrn}", response_model=PatientOut)
async def get_patient(
    mrn: str,
    hospital_id: int | None = Query(None, description="Required for platform_admin"),
    user: dict = Depends(require_role("hospital_admin", "dept_head", "receptionist", "doctor", "platform_admin")),
    jwt_hospital_id: int | None = Depends(current_hospital_id),
) -> dict:
    hid = _resolve_hospital(jwt_hospital_id, hospital_id, user["role"])
    patient = await get_patient_by_mrn(hid, mrn)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    return patient


@router.post("", response_model=PatientOut, status_code=201)
async def register_patient(
    body: PatientCreate, request: Request,
    hospital_id: int | None = Query(None, description="Required for platform_admin"),
    user: dict = Depends(require_role("hospital_admin", "receptionist", "platform_admin")),
    jwt_hospital_id: int | None = Depends(current_hospital_id),
) -> dict:
    hid = _resolve_hospital(jwt_hospital_id, hospital_id, user["role"])
    existing = await get_patient_by_phone(hid, body.phone)
    if existing:
        return JSONResponse(
            status_code=200,
            content=PatientOut.model_validate(existing).model_dump(mode="json"),
        )
    created = await create_patient(
        hospital_id=hid,
        name=body.name,
        phone=body.phone,
        age=body.age,
        gender=body.gender,
    )
    await audit_action(
        user, request, action="register_patient", entity_type="patient",
        entity_id=created.get("mrn") or created.get("id"),
        new_value={"name": body.name, "age": body.age, "gender": body.gender},
    )
    return created
