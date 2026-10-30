"""(Re-)register the WhatsApp number with the Cloud API — on demand.

The backend already does this automatically (startup + every
WHATSAPP_REGISTRATION_CHECK_MINUTES); this is the manual version.

    make whatsapp-register            # register only if not CONNECTED
    make whatsapp-register FORCE=1    # register even if CONNECTED
    python -m scripts.whatsapp_register [--force]

See tools/whatsapp_registration.py. Exit code 0 when the number ends up
CONNECTED, 1 otherwise.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from tools.whatsapp_registration import ensure_registered


async def register(force: bool = False) -> int:
    res = await ensure_registered(force=force)
    if res.before is None:
        print(f"Can't register: {res.error}. Set it in .env and restart the backend "
              "(docker compose up -d backend).", file=sys.stderr)
        return 1
    print(f"Number status: {res.before}")
    if not res.attempted:
        print("Already registered — nothing to do.")
        return 0
    print(f"Registered → status: {res.after}")
    if res.error:
        print(f"Problem: {res.error}", file=sys.stderr)
    return 0 if res.ok else 1


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--force", action="store_true", help="register even if already CONNECTED")
    sys.exit(asyncio.run(register(ap.parse_args().force)))


if __name__ == "__main__":
    main()
