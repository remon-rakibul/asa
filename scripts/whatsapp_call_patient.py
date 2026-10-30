"""Have the ASA voice agent call a patient on WhatsApp (business-initiated).

    docker compose exec backend python -m scripts.whatsapp_call_patient 8801XXXXXXXXX

The patient must have granted call permission to the business number in
WhatsApp. When they answer, the voice agent takes the call (same agent and
conversation thread as their WhatsApp chat). See docs/WHATSAPP_CALLING.md.
"""

from __future__ import annotations

import asyncio
import sys

from tools.whatsapp_calls import dial_patient


async def _main(to: str) -> int:
    res = await dial_patient(to)
    if not res:
        print("Call could not be placed — see backend logs.", file=sys.stderr)
        return 1
    print(f"Ringing: call {res['call_id']} in room {res['room_name']}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.exit(asyncio.run(_main(sys.argv[1])))
