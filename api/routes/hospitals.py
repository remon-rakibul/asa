"""Hospital and department management endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.deps import current_hospital_id, require_role
from api.schemas import (
    ChannelCreate, ChannelOut, DepartmentCreate, DepartmentOut,
    HospitalCreate, HospitalOut, HospitalUpdate,
)
from tools.audit import record_audit
from tools.database import (
    add_channel,
    create_clinic,
    create_hospital,
    get_hospital,
    list_departments,
    list_hospitals,
    update_clinic,
    update_hospital,
)

router = APIRouter(tags=["hospitals"])


@router.get("/hospitals", response_model=list[HospitalOut])
async def list_all_hospitals(
    _user: dict = Depends(require_role("platform_admin")),
) -> list[dict]:
    return await list_hospitals()


@router.post("/hospitals", response_model=HospitalOut, status_code=201)
async def create_new_hospital(
    body: HospitalCreate,
    _user: dict = Depends(require_role("platform_admin")),
) -> dict:
    hospital = await create_hospital(
        slug=body.slug,
        name=body.name,
        address=body.address,
        license_number=body.license_number,
        timezone=body.timezone,
    )
    if hospital is None:
        raise HTTPException(status_code=409, detail="Hospital slug already exists")
    return hospital


@router.get("/hospitals/{hospital_id}", response_model=HospitalOut)
async def get_one_hospital(
    hospital_id: int,
    user: dict = Depends(require_role("platform_admin", "hospital_admin")),
) -> dict:
    # hospital_admin can only view their own hospital.
    if user.get("role") == "hospital_admin" and user.get("hospital_id") != hospital_id:
        raise HTTPException(status_code=403, detail="Access denied")
    hospital = await get_hospital(hospital_id)
    if not hospital:
        raise HTTPException(status_code=404, detail="Hospital not found")
    return hospital


@router.patch("/hospitals/{hospital_id}", response_model=HospitalOut)
async def update_one_hospital(
    hospital_id: int,
    body: HospitalUpdate,
    user: dict = Depends(require_role("platform_admin", "hospital_admin")),
) -> dict:
    """Edit a hospital's details. hospital_admin may only edit their own hospital."""
    if user.get("role") == "hospital_admin" and user.get("hospital_id") != hospital_id:
        raise HTTPException(status_code=403, detail="Access denied")
    patch = body.model_dump(exclude_unset=True)
    updated = await update_hospital(hospital_id, **patch)
    if updated is None:
        raise HTTPException(status_code=404, detail="Hospital not found")
    await record_audit(
        action="update_hospital",
        entity_type="hospital",
        entity_id=str(hospital_id),
        hospital_id=hospital_id,
        user_id=int(user["sub"]),
        actor_role=user.get("role", ""),
        new_value=patch,
    )
    return updated


@router.get("/hospitals/{hospital_id}/departments", response_model=list[DepartmentOut])
async def list_hospital_departments(
    hospital_id: int,
    user: dict = Depends(require_role("platform_admin", "hospital_admin", "dept_head")),
) -> list[dict]:
    if user.get("role") not in ("platform_admin",) and user.get("hospital_id") != hospital_id:
        raise HTTPException(status_code=403, detail="Access denied")
    return await list_departments(hospital_id)


@router.post("/hospitals/{hospital_id}/departments", response_model=DepartmentOut, status_code=201)
async def create_department(
    hospital_id: int,
    body: DepartmentCreate,
    user: dict = Depends(require_role("platform_admin", "hospital_admin")),
) -> dict:
    """Create a new department (clinic) linked to this hospital."""
    if user.get("role") == "hospital_admin" and user.get("hospital_id") != hospital_id:
        raise HTTPException(status_code=403, detail="Access denied")

    clinic = await create_clinic(
        slug=body.slug,
        name=body.name,
        doctor_name=body.doctor_name,
        hospital_id=hospital_id,
    )
    if clinic is None:
        raise HTTPException(status_code=409, detail="Department slug already exists")

    # Patch the department-specific metadata columns (not in _CLINIC_COLS RETURNING).
    from tools.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE clinics SET specialty_code=$1, floor=$2, phone_ext=$3 WHERE id=$4",
            body.specialty_code, body.floor, body.phone_ext, clinic["id"],
        )
    clinic["hospital_id"] = hospital_id
    clinic["specialty_code"] = body.specialty_code
    clinic["floor"] = body.floor
    clinic["phone_ext"] = body.phone_ext
    return clinic


# --- IVR channel management for hospitals ---

def _check_hospital_access(user: dict, hospital_id: int) -> None:
    if user.get("role") == "hospital_admin" and user.get("hospital_id") != hospital_id:
        raise HTTPException(status_code=403, detail="Access denied")


@router.get("/hospitals/{hospital_id}/channels", response_model=list[ChannelOut])
async def list_hospital_channels(
    hospital_id: int,
    user: dict = Depends(require_role("platform_admin", "hospital_admin")),
):
    """List all IVR channels mapped to this hospital."""
    _check_hospital_access(user, hospital_id)
    from tools.database import get_pool, _CHANNEL_COLS
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT {_CHANNEL_COLS} FROM channels WHERE hospital_id = $1 ORDER BY id",
            hospital_id,
        )
    return [dict(r) for r in rows]


@router.post("/hospitals/{hospital_id}/channels", response_model=ChannelOut, status_code=201)
async def add_hospital_channel(
    hospital_id: int,
    body: ChannelCreate,
    user: dict = Depends(require_role("platform_admin", "hospital_admin")),
):
    """Map a phone number / DID to this hospital for IVR routing.
    Only voice_ivr kind is allowed here.
    """
    _check_hospital_access(user, hospital_id)
    if body.kind != "voice_ivr":
        raise HTTPException(
            status_code=400,
            detail="Only voice_ivr channels can be added at the hospital level.",
        )
    row = await add_channel(
        hospital_id=hospital_id,
        kind="voice_ivr",
        identifier=body.identifier.strip(),
        label=(body.label or "").strip() or None,
    )
    if row is None:
        raise HTTPException(status_code=409, detail="That number is already mapped.")
    return row


@router.delete("/hospitals/{hospital_id}/channels/{channel_id}", status_code=204)
async def remove_hospital_channel(
    hospital_id: int,
    channel_id: int,
    user: dict = Depends(require_role("platform_admin", "hospital_admin")),
):
    """Remove an IVR channel mapping from this hospital."""
    _check_hospital_access(user, hospital_id)
    from tools.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM channels WHERE id = $1 AND hospital_id = $2",
            channel_id, hospital_id,
        )
    try:
        deleted = int(result.split()[-1]) > 0
    except (ValueError, IndexError):
        deleted = False
    if not deleted:
        raise HTTPException(status_code=404, detail="Channel not found")
