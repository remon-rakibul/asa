"""Shared FastAPI dependencies: tenant resolution, admin auth, platform auth."""

from __future__ import annotations

import hmac

from fastapi import Depends, Header, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config import settings
from tools.auth import decode_token
from tools.database import get_clinic_id_by_channel, get_default_clinic_id

bearer_scheme = HTTPBearer(auto_error=False, description="Paste the JWT from /auth/login")


async def resolve_channel_clinic(kind: str, identifier: str) -> int:
    """Map a patient channel identity to a clinic.

    When strict_channel_routing is True (recommended for multi-tenant), unknown
    identifiers raise 404 instead of silently routing to the default clinic.
    """
    cid = await get_clinic_id_by_channel(kind, identifier)
    if cid is not None:
        return cid
    if settings.strict_channel_routing:
        raise HTTPException(
            status_code=404,
            detail=f"No clinic is mapped to {kind} identifier '{identifier}'. "
                   "Add the channel mapping in the admin dashboard.",
        )
    return await get_default_clinic_id()


async def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict:
    """Decode the Bearer JWT and return its claims, or 401."""
    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    claims = decode_token(credentials.credentials)
    if not claims or "user_id" not in claims:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return claims


async def current_clinic_id(user: dict = Depends(current_user)) -> int:
    """The clinic the authenticated admin belongs to."""
    cid = user.get("clinic_id")
    if not cid:
        raise HTTPException(status_code=403, detail="This account is not scoped to a clinic")
    return int(cid)


async def current_patient(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict:
    """Decode a patient JWT and return {account_id}, or 401/403.

    Rejects staff tokens (kind != 'patient') so the patient portal endpoints are
    reachable only by patient accounts.
    """
    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    claims = decode_token(credentials.credentials)
    if not claims or claims.get("kind") != "patient" or "account_id" not in claims:
        raise HTTPException(status_code=401, detail="Invalid or expired patient token")
    return {"account_id": int(claims["account_id"])}


async def require_platform_admin(
    x_platform_key: str | None = Header(None, alias="X-Platform-Key"),
) -> None:
    """Require the platform admin key for cross-tenant operations (POST /clinics).

    Set PLATFORM_ADMIN_KEY in .env.  Empty = clinic creation disabled.
    """
    if not settings.platform_admin_key:
        raise HTTPException(
            status_code=503,
            detail="Clinic provisioning is not enabled on this server. "
                   "Set PLATFORM_ADMIN_KEY to enable it.",
        )
    if not hmac.compare_digest(x_platform_key or "", settings.platform_admin_key):
        raise HTTPException(status_code=403, detail="Invalid platform admin key")


def require_role(*allowed_roles: str):
    """Dependency factory that enforces a role allowlist on the JWT claims.

    Usage::

        @router.get("/admin-only")
        async def endpoint(user: dict = Depends(require_role("hospital_admin", "platform_admin"))):
            ...
    """
    async def _check(user: dict = Depends(current_user)) -> dict:
        role = user.get("role", "")
        if role not in allowed_roles:
            raise HTTPException(
                status_code=403,
                detail=f"Role '{role}' is not permitted. Required: {list(allowed_roles)}",
            )
        return user
    return _check


async def current_hospital_id(user: dict = Depends(current_user)) -> int | None:
    """Extract the hospital_id from JWT claims (None for standalone-clinic users)."""
    hid = user.get("hospital_id")
    return int(hid) if hid else None


def client_ip(request: Request) -> str | None:
    """Best-effort caller IP for rate limiting and the audit trail.

    Only honours X-Forwarded-For when TRUST_PROXY_HEADERS is set — i.e. the app
    is deployed behind a reverse proxy that appends the real client IP. On a
    direct-to-internet deployment the header is attacker-controlled, so trusting
    it would let a client forge a fresh IP per request and bypass rate limits
    entirely; there we use the socket peer instead.
    """
    if settings.trust_proxy_headers:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


async def audit_action(
    user: dict, request: Request, *,
    action: str, entity_type: str, entity_id=None,
    clinic_id: int | None = None, old_value=None, new_value=None,
) -> None:
    """Record a staff write action to the audit log, pulling actor + IP from the
    request. Best-effort (record_audit never raises)."""
    from tools.audit import record_audit  # local import avoids import-time cycle

    uid = user.get("user_id")
    await record_audit(
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        clinic_id=clinic_id if clinic_id is not None else user.get("clinic_id"),
        hospital_id=user.get("hospital_id"),
        user_id=int(uid) if uid else None,
        actor_role=user.get("role", ""),
        old_value=old_value,
        new_value=new_value,
        ip_address=client_ip(request),
    )
