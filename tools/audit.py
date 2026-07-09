"""Append-only audit log for admin write actions.

Never raises — audit failures are logged but never block the operation.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Any, Optional

from .database import get_pool

log = logging.getLogger(__name__)


async def record_audit(
    *,
    action: str,
    entity_type: str,
    entity_id: Optional[str] = None,
    clinic_id: Optional[int] = None,
    hospital_id: Optional[int] = None,
    user_id: Optional[int] = None,
    actor_role: str = "",
    old_value: Optional[Any] = None,
    new_value: Optional[Any] = None,
    ip_address: Optional[str] = None,
) -> None:
    """Best-effort insert into audit_log.  Fire-and-forget; never raises."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO audit_log "
                "(hospital_id, clinic_id, user_id, actor_role, action, "
                " entity_type, entity_id, old_value, new_value, ip_address) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
                hospital_id,
                clinic_id,
                user_id,
                actor_role,
                action,
                entity_type,
                str(entity_id) if entity_id is not None else None,
                json.dumps(old_value) if old_value is not None else None,
                json.dumps(new_value) if new_value is not None else None,
                ip_address,
            )
    except Exception:
        log.warning("audit_log insert failed", exc_info=True)


async def list_audit_log(
    *,
    hospital_id: Optional[int] = None,
    clinic_id: Optional[int] = None,
    entity_type: Optional[str] = None,
    user_id: Optional[int] = None,
    action: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 100,
) -> list[dict]:
    pool = await get_pool()
    conditions: list[str] = []
    params: list = []

    def _p(v) -> str:
        params.append(v)
        return f"${len(params)}"

    if hospital_id is not None:
        conditions.append(f"a.hospital_id = {_p(hospital_id)}")
    if clinic_id is not None:
        conditions.append(f"a.clinic_id = {_p(clinic_id)}")
    if entity_type:
        conditions.append(f"a.entity_type = {_p(entity_type)}")
    if user_id is not None:
        conditions.append(f"a.user_id = {_p(user_id)}")
    if action:
        conditions.append(f"a.action = {_p(action)}")
    # asyncpg binds date/timestamp params as date objects, not strings.
    if date_from:
        conditions.append(f"a.created_at >= {_p(date.fromisoformat(date_from))}")
    if date_to:
        conditions.append(
            f"a.created_at < {_p(date.fromisoformat(date_to) + timedelta(days=1))}"
        )

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    query = (
        f"SELECT a.id, a.hospital_id, a.clinic_id, a.user_id, a.actor_role, "
        f"a.action, a.entity_type, a.entity_id, a.old_value, a.new_value, "
        f"a.ip_address, a.created_at, u.email AS actor_email "
        f"FROM audit_log a LEFT JOIN users u ON u.id = a.user_id {where} "
        f"ORDER BY a.created_at DESC LIMIT {int(limit)}"
    )
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *params)
    return [dict(r) for r in rows]


async def list_audit_actions(
    *, hospital_id: Optional[int] = None, clinic_id: Optional[int] = None,
) -> dict:
    """Distinct action + entity_type values (for the audit filter dropdowns)."""
    pool = await get_pool()
    conditions: list[str] = []
    params: list = []

    def _p(v) -> str:
        params.append(v)
        return f"${len(params)}"

    if hospital_id is not None:
        conditions.append(f"hospital_id = {_p(hospital_id)}")
    if clinic_id is not None:
        conditions.append(f"clinic_id = {_p(clinic_id)}")
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    async with pool.acquire() as conn:
        actions = await conn.fetch(
            f"SELECT DISTINCT action FROM audit_log {where} ORDER BY action", *params
        )
        entities = await conn.fetch(
            f"SELECT DISTINCT entity_type FROM audit_log {where} ORDER BY entity_type",
            *params,
        )
    return {
        "actions": [r["action"] for r in actions],
        "entity_types": [r["entity_type"] for r in entities],
    }
