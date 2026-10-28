"""Encrypt patient PII rows written before PATIENT_ENCRYPTION_KEY was set.

New rows are encrypted on write (tools/crypto.py); rows from before the key
existed stay plaintext — readable (decrypt_field passes them through) but not
protected. This one-off backfill encrypts them in place.

Idempotent: values that already decrypt with the current key are skipped, so
re-running is safe. Runs in one transaction per table.

    make encrypt-pii           # inside the backend container
    python -m scripts.encrypt_existing_pii --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from cryptography.fernet import InvalidToken

from tools.crypto import _get_fernet
from tools.database import close_pool, get_pool

# table -> encrypted columns (must match the encrypt_field() call sites)
TARGETS: dict[str, tuple[str, ...]] = {
    "appointments": ("patient_name", "patient_mobile"),
    "sms_log": ("to_number", "body"),
    "outbound_messages": ("to_number", "body"),
}


def _is_ciphertext(fernet, value: str) -> bool:
    try:
        fernet.decrypt(value.encode("ascii"))
        return True
    except (InvalidToken, UnicodeEncodeError, ValueError):
        return False


async def backfill(dry_run: bool) -> int:
    fernet = _get_fernet()
    if fernet is None:
        print("PATIENT_ENCRYPTION_KEY is not set — nothing to do.", file=sys.stderr)
        return 1
    pool = await get_pool()
    total = 0
    try:
        for table, cols in TARGETS.items():
            async with pool.acquire() as conn, conn.transaction():
                rows = await conn.fetch(f"SELECT id, {', '.join(cols)} FROM {table} FOR UPDATE")
                changed = 0
                for r in rows:
                    updates = {
                        c: fernet.encrypt(r[c].encode("utf-8")).decode("ascii")
                        for c in cols
                        if r[c] is not None and not _is_ciphertext(fernet, r[c])
                    }
                    if not updates:
                        continue
                    changed += 1
                    if not dry_run:
                        sets = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(updates))
                        await conn.execute(
                            f"UPDATE {table} SET {sets} WHERE id = $1", r["id"], *updates.values(),
                        )
                print(f"{table}: {changed}/{len(rows)} row(s) {'would be ' if dry_run else ''}encrypted")
                total += changed
    finally:
        await close_pool()
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="report only, change nothing")
    sys.exit(asyncio.run(backfill(ap.parse_args().dry_run)))


if __name__ == "__main__":
    main()
