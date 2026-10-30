"""scripts/whatsapp_register.py — registers only when needed, needs a 6-digit PIN."""

from __future__ import annotations

import json

import httpx
import pytest

from scripts import whatsapp_register as wr
from tools import whatsapp_registration as reg_mod


@pytest.fixture(autouse=True)
def _wa(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "whatsapp_token", "tok")
    monkeypatch.setattr(settings, "whatsapp_phone_id", "111")
    monkeypatch.setattr(settings, "whatsapp_registration_pin", "123456")


def _meta(monkeypatch, statuses, register=None):
    """Fake Graph API: GET returns the next status; POST /register runs `register`."""
    seen = {"register": []}
    statuses = list(statuses)

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST" and req.url.path.endswith("/register"):
            seen["register"].append(json.loads(req.content))
            if isinstance(register, Exception):
                raise register
            return httpx.Response(200, json={"success": True})
        return httpx.Response(200, json={"status": statuses.pop(0)})

    real = httpx.AsyncClient
    monkeypatch.setattr(reg_mod.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return seen


async def test_connected_number_is_left_alone(monkeypatch):
    seen = _meta(monkeypatch, ["CONNECTED"])
    assert await wr.register() == 0
    assert seen["register"] == []


async def test_pending_number_is_registered_with_pin(monkeypatch):
    seen = _meta(monkeypatch, ["PENDING", "CONNECTED"])
    assert await wr.register() == 0
    assert seen["register"] == [{"messaging_product": "whatsapp", "pin": "123456"}]


async def test_timeout_still_checks_status_and_failure_exits_1(monkeypatch):
    _meta(monkeypatch, ["PENDING", "PENDING"], register=httpx.ReadTimeout("slow"))
    assert await wr.register() == 1


async def test_missing_or_bad_pin_refuses(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "whatsapp_registration_pin", "12ab")
    assert await wr.register() == 1


# --------------------------------------------------------------------------- #
# Backend watchdog (api/app.py::_whatsapp_registration_loop)
# --------------------------------------------------------------------------- #

async def test_watchdog_reregisters_when_dropped(monkeypatch):
    import asyncio
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock

    import api.app as app_mod
    from config import settings

    settings.whatsapp_registration_check_minutes = 30
    seen = _meta(monkeypatch, ["PENDING", "CONNECTED"])

    class _Conn:
        async def fetchval(self, sql, *a):
            return True

    class _Pool:
        @asynccontextmanager
        async def acquire(self):
            yield _Conn()

    monkeypatch.setattr(app_mod, "get_pool", AsyncMock(return_value=_Pool()))

    async def stop(_):            # end the loop after the first cycle
        raise asyncio.CancelledError

    monkeypatch.setattr(app_mod.asyncio, "sleep", stop)
    with pytest.raises(asyncio.CancelledError):
        await app_mod._whatsapp_registration_loop()
    assert seen["register"] == [{"messaging_product": "whatsapp", "pin": "123456"}]


async def test_watchdog_disabled_without_pin_or_interval(monkeypatch):
    import api.app as app_mod
    from config import settings

    seen = _meta(monkeypatch, ["PENDING"])
    settings.whatsapp_registration_check_minutes = 0
    await app_mod._whatsapp_registration_loop()          # returns immediately
    settings.whatsapp_registration_check_minutes = 30
    monkeypatch.setattr(settings, "whatsapp_registration_pin", "")
    await app_mod._whatsapp_registration_loop()
    assert seen["register"] == []
