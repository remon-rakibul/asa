"""Create or reset an admin user.

Usage:
  # Clinic admin (default):
  python -m scripts.create_admin --email admin@clinic.bd --password secret123
  python -m scripts.create_admin --email a@b.bd --password pw --clinic-slug default

  # Platform super-admin (runs the whole marketplace — money dashboards,
  # per-hospital wallets, everything). Not scoped to any clinic:
  python -m scripts.create_admin --email you@asa.bd --password 'strong-pw' --superadmin

Defaults to the seeded 'default' clinic for a clinic admin.
"""

from __future__ import annotations

import argparse
import asyncio

from tools.auth import hash_password
from tools.database import (
    close_pool,
    create_user,
    get_default_clinic_id,
    get_pool,
    list_clinics,
)


async def _clinic_id_for_slug(slug: str | None) -> int:
    if not slug:
        return await get_default_clinic_id()
    for c in await list_clinics():
        if c["slug"] == slug:
            return c["id"]
    raise SystemExit(f"No clinic with slug '{slug}'. Existing: "
                     + ", ".join(c["slug"] for c in await list_clinics()))


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--clinic-slug", default=None)
    ap.add_argument(
        "--superadmin", action="store_true",
        help="Create a platform super-admin (role=platform_admin, no clinic) "
             "instead of a clinic admin.",
    )
    args = ap.parse_args()

    await get_pool()
    try:
        if args.superadmin:
            user = await create_user(
                email=args.email,
                password_hash=hash_password(args.password),
                role="platform_admin",
            )
            if user is None:
                raise SystemExit(f"User {args.email} already exists.")
            print(f"Created platform super-admin {args.email} (role=platform_admin)")
            return
        clinic_id = await _clinic_id_for_slug(args.clinic_slug)
        user = await create_user(
            clinic_id=clinic_id,
            email=args.email,
            password_hash=hash_password(args.password),
        )
        if user is None:
            raise SystemExit(f"User {args.email} already exists.")
        print(f"Created admin {args.email} for clinic_id={clinic_id}")
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
