"""Admin authentication: password hashing + JWT issue/verify/revoke.

Tokens carry the user's id, clinic_id, role, and a unique jti so individual
tokens can be revoked without invalidating all sessions.  The in-process
blacklist works for single-worker deployments; back it with Redis (store jti
in a sorted set with TTL == jwt_ttl_hours) for multi-process/multi-pod.
"""

from __future__ import annotations

import datetime as dt
import time
import uuid
from typing import Optional

import bcrypt
import jwt

from config import settings

_ALGO = "HS256"

# In-process JWT blacklist: maps jti → expiry (Unix timestamp).
# Expired entries are swept on each revocation so memory stays bounded.
# Lost on restart, which is acceptable (tokens expire anyway within jwt_ttl_hours).
# Replace with Redis sorted-set for multi-worker HA deployments.
_blacklist: dict[str, float] = {}


def _to_bytes(password: str) -> bytes:
    # bcrypt accepts at most 72 bytes; truncate deterministically.
    return password.encode("utf-8")[:72]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_to_bytes(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_to_bytes(password), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_token(
    *, user_id: int, clinic_id: Optional[int] = None, role: str = "hospital_admin",
    hospital_id: Optional[int] = None
) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    payload = {
        "sub": str(user_id),
        "user_id": user_id,
        "clinic_id": clinic_id,
        "role": role,
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": now + dt.timedelta(hours=settings.jwt_ttl_hours),
    }
    if hospital_id is not None:
        payload["hospital_id"] = hospital_id
    return jwt.encode(payload, settings.jwt_secret, algorithm=_ALGO)


def create_patient_token(*, account_id: int) -> str:
    """Issue a JWT for a patient self-service account.

    Carries kind='patient' + account_id (no staff role/clinic claims) so it can
    never satisfy the admin `require_role` / `current_clinic_id` dependencies.
    """
    now = dt.datetime.now(dt.timezone.utc)
    payload = {
        "sub": f"patient:{account_id}",
        "kind": "patient",
        "account_id": account_id,
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": now + dt.timedelta(hours=settings.jwt_ttl_hours),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=_ALGO)


def decode_token(token: str) -> Optional[dict]:
    try:
        claims = jwt.decode(token, settings.jwt_secret, algorithms=[_ALGO])
    except jwt.PyJWTError:
        return None
    # Reject if the token has been explicitly revoked.
    exp = _blacklist.get(claims.get("jti", ""))
    if exp is not None and time.time() < exp:
        return None
    return claims


def revoke_token(claims: dict) -> None:
    """Add this token's jti to the blacklist so it cannot be reused.

    Sweeps entries whose expiry has already passed to prevent unbounded growth.
    """
    jti = claims.get("jti")
    if not jti:
        return
    exp = claims.get("exp")
    if exp:
        # Sweep expired entries before adding the new one.
        now = time.time()
        expired = [k for k, v in _blacklist.items() if v <= now]
        for k in expired:
            del _blacklist[k]
        _blacklist[jti] = float(exp)
