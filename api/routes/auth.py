"""Admin authentication + clinic management endpoints.

POST /auth/login          -> JWT for an existing admin user
POST /auth/logout         -> revoke the current token (blacklist its jti)
GET  /auth/me             -> current clinic config
PATCH /clinics/me         -> edit clinic settings
GET  /clinics             -> list all clinics (admin)
POST /clinics             -> create a clinic + first admin (requires X-Platform-Key)
POST /hospitals/signup    -> PUBLIC hospital admin self-signup + free trial month
POST /platform-admins     -> create a platform_admin account (requires X-Platform-Key)
"""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request

from agent.nodes import invalidate_clinic
from config import settings
from tools.auth import create_token, hash_password, revoke_token, verify_password
from tools.audit import record_audit
from tools.database import (
    create_hospital_tenant,
    create_user,
    get_clinic,
    get_user_by_email,
    list_clinics,
    update_clinic,
)

from ..deps import (
    client_ip, current_clinic_id, current_user, require_platform_admin, require_role,
)
from ..schemas import (
    ClinicCreate, ClinicOut, ClinicUpdate, LoginRequest, TokenResponse,
    PlatformAdminCreate, PlatformAdminOut,
)

router = APIRouter(tags=["auth"])


@router.post("/auth/login", response_model=TokenResponse)
async def login(body: LoginRequest, request: Request):
    user = await get_user_by_email(body.email)
    if not user or not verify_password(body.password, user["password_hash"]):
        await record_audit(
            action="login_failed", entity_type="user", entity_id=body.email,
            actor_role="", ip_address=client_ip(request),
        )
        raise HTTPException(status_code=401, detail="Invalid email or password")
    # Block login for users belonging to a suspended clinic.
    if user.get("clinic_id"):
        clinic = await get_clinic(user["clinic_id"])
        if clinic and clinic.get("status") == "suspended":
            raise HTTPException(status_code=403, detail="Clinic account is suspended")
    token = create_token(
        user_id=user["id"],
        clinic_id=user["clinic_id"],
        role=user["role"],
        hospital_id=user.get("hospital_id"),
    )
    await record_audit(
        action="login", entity_type="user", entity_id=str(user["id"]),
        clinic_id=user.get("clinic_id"), hospital_id=user.get("hospital_id"),
        user_id=user["id"], actor_role=user.get("role", ""),
        ip_address=client_ip(request),
    )
    return TokenResponse(access_token=token, clinic_id=user["clinic_id"])


@router.post("/auth/logout", status_code=204)
async def logout(request: Request, user: dict = Depends(current_user)):
    """Revoke the current token. The jti is blacklisted for the remainder of
    its TTL (in-process; use Redis for multi-worker deployments)."""
    revoke_token(user)
    uid = user.get("user_id")
    await record_audit(
        action="logout", entity_type="user",
        entity_id=str(uid) if uid else None,
        clinic_id=user.get("clinic_id"), hospital_id=user.get("hospital_id"),
        user_id=int(uid) if uid else None, actor_role=user.get("role", ""),
        ip_address=client_ip(request),
    )


@router.get("/auth/me", response_model=ClinicOut)
async def me(user: dict = Depends(current_user)):
    clinic_id = user.get("clinic_id")
    if clinic_id:
        clinic = await get_clinic(int(clinic_id))
        if not clinic:
            raise HTTPException(status_code=404, detail="Clinic not found")
        return {**clinic, "role": user.get("role")}
    # platform_admin with no clinic — return a minimal synthetic record
    return ClinicOut(
        id=0,
        slug="platform",
        name="Platform Admin",
        doctor_name="",
        doctor_phone="",
        timezone="UTC",
        availability_days_ahead=7,
        status="active",
        role=user.get("role"),
    )


@router.patch("/clinics/me", response_model=ClinicOut)
async def update_my_clinic(
    body: ClinicUpdate,
    clinic_id: int = Depends(current_clinic_id),
    user: dict = Depends(current_user),
):
    """Edit the logged-in clinic's settings (branding, availability, greeting)."""
    updated = await update_clinic(clinic_id, **body.model_dump(exclude_unset=True))
    if updated is None:
        raise HTTPException(status_code=404, detail="Clinic not found")
    invalidate_clinic(clinic_id)
    await record_audit(
        action="update_clinic",
        entity_type="clinic",
        entity_id=str(clinic_id),
        clinic_id=clinic_id,
        user_id=int(user["sub"]),
        actor_role=user.get("role", ""),
        new_value=body.model_dump(exclude_unset=True),
    )
    return updated


@router.get("/clinics", response_model=list[ClinicOut])
async def get_clinics(_: dict = Depends(require_role("platform_admin"))):
    """List all tenants — platform admins only."""
    return await list_clinics()


@router.post("/clinics", response_model=ClinicOut, status_code=201)
async def add_clinic(
    body: ClinicCreate,
    _: None = Depends(require_platform_admin),
):
    """Create a clinic and its first admin user.

    Requires the X-Platform-Key header matching PLATFORM_ADMIN_KEY in .env.
    """
    # Atomic provisioning (hospital + clinic + admin), so a mid-way failure
    # can't orphan a half-created tenant. No trial subscription here — that's
    # the public self-signup's perk (trial_days omitted -> no subscription row).
    if await get_user_by_email(body.admin_email):
        raise HTTPException(status_code=409, detail="Admin email already in use")
    try:
        tenant = await create_hospital_tenant(
            slug=body.slug,
            name=body.name,
            doctor_name=body.doctor_name,
            doctor_phone=body.doctor_phone,
            availability_days_ahead=body.availability_days_ahead,
            admin_email=body.admin_email,
            admin_password_hash=hash_password(body.admin_password),
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(
            status_code=409, detail="Clinic slug or admin email already exists"
        )
    return tenant["clinic"]


@router.post("/hospitals/signup", response_model=TokenResponse, status_code=201)
async def hospital_signup(body: ClinicCreate):
    """Public hospital admin self-signup — no platform key needed.

    Creates the hospital + its first department + the admin login, and
    starts a FREE first-month subscription (no payment collected). After
    the free period a monthly fee applies: the hourly billing sweep marks a
    lapsed subscription past_due (grace window), then suspended — hidden
    from patient search/booking until a platform admin marks it paid.
    """
    # Pre-check for a specific, friendly error in the common case. The
    # transaction inside create_hospital_tenant is the real safety net: it makes
    # the whole provisioning atomic, so a race (or any mid-way failure) can
    # never leave an orphaned, admin-less hospital that blocks the name forever.
    if await get_user_by_email(body.admin_email):
        raise HTTPException(status_code=409, detail="Admin email already in use")

    try:
        tenant = await create_hospital_tenant(
            slug=body.slug,
            name=body.name,
            doctor_name=body.doctor_name,
            doctor_phone=body.doctor_phone,
            availability_days_ahead=body.availability_days_ahead,
            admin_email=body.admin_email,
            admin_password_hash=hash_password(body.admin_password),
            monthly_fee=settings.hospital_subscription_fee,
            trial_days=settings.hospital_trial_days,
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(
            status_code=409,
            detail="That hospital name or admin email is already taken",
        )

    user = tenant["user"]
    token = create_token(
        user_id=user["id"], clinic_id=user["clinic_id"],
        role=user["role"], hospital_id=user["hospital_id"],
    )
    return TokenResponse(access_token=token, clinic_id=user["clinic_id"])


@router.post("/platform-admins", response_model=PlatformAdminOut, status_code=201)
async def create_platform_admin(
    body: PlatformAdminCreate,
    _: None = Depends(require_platform_admin),
):
    """Create a platform_admin account. Requires the X-Platform-Key header."""
    user = await create_user(
        email=body.email,
        password_hash=hash_password(body.password),
        role="platform_admin",
    )
    if user is None:
        raise HTTPException(status_code=409, detail="Email already in use")
    return user
