"""Integration probes (tools/integration_checks.py), /health, and the
platform-admin verify endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

import tools.integration_checks as ic
from config import settings


@pytest.fixture
def restore_settings():
    keys = ("llm_provider", "openrouter_api_key", "gemini_api_key", "sms_provider",
            "whatsapp_token", "whatsapp_app_secret", "payment_provider", "booking_fee_default")
    saved = {k: getattr(settings, k) for k in keys}
    yield
    for k, v in saved.items():
        setattr(settings, k, v)


def test_effective_provider_reports_the_ollama_fallback(restore_settings):
    # _build_chat_model silently falls back to Ollama without a key — the
    # status page must report what actually runs, not the configured wish.
    settings.llm_provider, settings.openrouter_api_key = "openrouter", ""
    assert ic.effective_llm_provider()[0] == "ollama"
    settings.openrouter_api_key = "sk-x"
    assert ic.effective_llm_provider() == ("openrouter", settings.openrouter_model)


@pytest.mark.asyncio
async def test_sms_none_is_not_configured_and_warned(restore_settings):
    settings.sms_provider = "none"
    r = await ic.check_sms()
    assert r["status"] == ic.NOT_CONFIGURED
    assert "NOT sent" in r["detail"]
    assert any("SMS_PROVIDER=none" in w for w in ic.config_warnings())


def test_safe_error_never_echoes_urls_or_keys():
    req = httpx.Request("GET", "https://api.example.com/x?key=SECRET123")
    exc = httpx.HTTPStatusError("boom SECRET123", request=req, response=httpx.Response(401, request=req))
    assert ic._safe_error(exc) == "HTTP 401"
    assert "SECRET" not in ic._safe_error(httpx.ConnectError("https://h/?key=SECRET123"))


@pytest.mark.asyncio
async def test_run_checks_turns_a_crashing_probe_into_an_error_row():
    async def check_boom():
        raise RuntimeError("kaboom")

    async def check_fine():
        return ic._result("fine", "Fine", ic.OK, "")

    rows = await ic.run_checks([check_boom, check_fine])
    assert rows[0] == {**rows[0], "key": "boom", "status": ic.ERROR, "detail": "RuntimeError"}
    assert rows[1]["status"] == ic.OK


@pytest.mark.asyncio
async def test_check_llm_openrouter_invalid_key_is_error(restore_settings):
    settings.llm_provider, settings.openrouter_api_key = "openrouter", "sk-bad"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/key")
        return httpx.Response(401, json={"error": "invalid"})

    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient
    with patch.object(ic.httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw)):
        r = await ic.check_llm()
    assert r["status"] == ic.ERROR and r["detail"] == "HTTP 401"


@pytest.fixture
def client():
    from api.app import app, _ready_cache

    _ready_cache.update(at=0.0, body=None)
    with (
        patch("api.app.build_graph", new_callable=AsyncMock, return_value=MagicMock()),
        patch("api.app.get_pool", new_callable=AsyncMock, return_value=MagicMock()),
        patch("api.app.close_pool", new_callable=AsyncMock),
        patch("tools.reminders.send_pending_reminders", new_callable=AsyncMock, return_value=0),
    ):
        with TestClient(app) as c:
            yield c
    from api.deps import current_user
    app.dependency_overrides.pop(current_user, None)
    _ready_cache.update(at=0.0, body=None)


def _as(claims: dict):
    from api.app import app
    from api.deps import current_user
    app.dependency_overrides[current_user] = lambda: claims


def test_health_live_has_no_dependencies(client):
    assert client.get("/health/live").json() == {"status": "ok"}


def test_health_checks_the_active_llm_not_ollama(client):
    rows = [ic._result("database", "PostgreSQL", ic.OK, ""),
            ic._result("llm", "LLM (openrouter: m)", ic.ERROR, "HTTP 401")]
    with patch("tools.integration_checks.run_checks", AsyncMock(return_value=rows)):
        body = client.get("/health").json()
    assert body["status"] == "degraded"
    assert body["checks"] == {"database": "ok", "llm": "error: HTTP 401"}


def test_verify_requires_platform_admin(client):
    _as({"user_id": 2, "role": "hospital_admin", "clinic_id": 1})
    assert client.get("/platform/integrations/verify").status_code == 403


def test_verify_reports_failing_integrations(client):
    _as({"user_id": 1, "role": "platform_admin"})
    rows = [ic._result("database", "PostgreSQL", ic.OK, ""),
            ic._result("sms", "SMS (none)", ic.NOT_CONFIGURED, "off"),
            ic._result("whatsapp", "WhatsApp", ic.ERROR, "HTTP 401")]
    with patch("tools.integration_checks.run_checks", AsyncMock(return_value=rows)):
        body = client.get("/platform/integrations/verify").json()
    # not_configured is a choice, not a failure; a configured-but-broken one is.
    assert body["status"] == "failing" and body["failing"] == ["whatsapp"]
    assert isinstance(body["warnings"], list)
