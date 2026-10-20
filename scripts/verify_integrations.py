"""Live-check every external integration from the command line.

    make verify            # inside the running backend container
    python -m scripts.verify_integrations [--json]

Same probes as GET /platform/integrations/verify. Exits 1 when any configured
integration is failing (not_configured alone does not fail), so it can gate a
deploy.
"""

from __future__ import annotations

import asyncio
import json
import sys

from tools.integration_checks import ERROR, NOT_CONFIGURED, config_warnings, run_checks

_ICON = {"ok": "OK  ", NOT_CONFIGURED: "OFF ", ERROR: "FAIL"}


async def _main(as_json: bool) -> int:
    rows = await run_checks()
    warnings = config_warnings()
    if as_json:
        print(json.dumps({"checks": rows, "warnings": warnings}, ensure_ascii=False, indent=2))
    else:
        for r in rows:
            ms = f"{r['latency_ms']}ms" if r["latency_ms"] is not None else ""
            print(f"[{_ICON[r['status']]}] {r['name']:<42} {ms:>7}  {r['detail']}")
        for w in warnings:
            print(f"[WARN] {w}")
    return 1 if any(r["status"] == ERROR for r in rows) else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main("--json" in sys.argv)))
