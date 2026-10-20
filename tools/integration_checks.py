"""Live, side-effect-free probes for every external integration.

Each probe makes the cheapest *real* call that proves the credentials and the
network path work (an authenticated read — never a send, booking, or charge)
and returns a uniform result dict:

    {"key", "name", "status", "detail", "latency_ms"}

status is one of:
  ok             — the live call succeeded
  not_configured — the integration is off/unset (fine if that's intended)
  error          — configured, but the live call failed (detail says why)

Used by /health (a cheap subset) and GET /platform/integrations/verify (all).
Details never contain secrets: error text is reduced to the exception type and
an HTTP status, because some client errors echo the request URL.
"""

from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable

import httpx

from config import settings

OK, NOT_CONFIGURED, ERROR = "ok", "not_configured", "error"
_TIMEOUT = 8.0


def _result(key: str, name: str, status: str, detail: str, t0: float | None = None) -> dict:
    latency = round((time.perf_counter() - t0) * 1000) if t0 is not None else None
    return {"key": key, "name": name, "status": status, "detail": detail, "latency_ms": latency}


def _safe_error(exc: BaseException) -> str:
    """Exception summary that can't leak a key embedded in a URL or message."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return type(exc).__name__


def effective_llm_provider() -> tuple[str, str]:
    """(provider, model) the agent ACTUALLY uses.

    Mirrors agent/nodes.py::_build_chat_model, which silently falls back to
    local Ollama when the selected cloud provider has no API key — so the
    status page must report the fallback, not the configured wish.
    """
    provider = (settings.llm_provider or "ollama").lower()
    if provider == "openrouter" and settings.openrouter_api_key:
        return "openrouter", settings.openrouter_model
    if provider == "gemini" and settings.gemini_api_key:
        return "gemini", settings.gemini_llm_model
    return "ollama", settings.ollama_model


# --------------------------------------------------------------------------- #
# Probes
# --------------------------------------------------------------------------- #

async def check_database() -> dict:
    from tools.database import get_pool

    t0 = time.perf_counter()
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
            has_vector = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')"
            )
            head = await conn.fetchval("SELECT version_num FROM alembic_version LIMIT 1")
        detail = f"migration {head or '?'}" + ("" if has_vector else "; pgvector extension MISSING")
        return _result("database", "PostgreSQL", OK if has_vector else ERROR, detail, t0)
    except Exception as exc:
        return _result("database", "PostgreSQL", ERROR, _safe_error(exc), t0)


async def check_llm() -> dict:
    """Authenticated read against the active LLM provider (no tokens spent)."""
    provider, model = effective_llm_provider()
    name = f"LLM ({provider}: {model})"
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            if provider == "openrouter":
                # /key returns the key's own metadata — 401 if revoked/invalid.
                r = await client.get(
                    f"{settings.openrouter_base_url.rstrip('/')}/key",
                    headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
                )
                r.raise_for_status()
                return _result("llm", name, OK, "key valid", t0)
            if provider == "gemini":
                r = await client.get(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}",
                    headers={"x-goog-api-key": settings.gemini_api_key},
                )
                r.raise_for_status()
                return _result("llm", name, OK, "model reachable", t0)
            r = await client.get(f"{settings.ollama_base_url}/api/tags")
            r.raise_for_status()
            names = {m.get("name") for m in r.json().get("models", [])}
            if model not in names:
                return _result("llm", name, ERROR, f"model '{model}' not pulled", t0)
            return _result("llm", name, OK, "model loaded", t0)
    except Exception as exc:
        return _result("llm", name, ERROR, _safe_error(exc), t0)


async def check_embeddings() -> dict:
    """RAG embeddings always run on Ollama (tools/rag.py), whatever the chat LLM."""
    name = f"Embeddings (ollama: {settings.embedding_model})"
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(f"{settings.ollama_base_url}/api/tags")
            r.raise_for_status()
        names = {m.get("name", "").split(":")[0] for m in r.json().get("models", [])}
        if settings.embedding_model.split(":")[0] not in names:
            return _result("embeddings", name, ERROR, "embedding model not pulled", t0)
        return _result("embeddings", name, OK, "model loaded", t0)
    except Exception as exc:
        return _result("embeddings", name, ERROR, _safe_error(exc), t0)


async def check_redis() -> dict:
    if not settings.redis_url:
        return _result("redis", "Redis", NOT_CONFIGURED, "in-process rate limiter (single worker only)")
    t0 = time.perf_counter()
    try:
        import redis.asyncio as redis

        client = redis.from_url(settings.redis_url)
        try:
            await client.ping()
        finally:
            await client.aclose()
        return _result("redis", "Redis", OK, "ping", t0)
    except Exception as exc:
        return _result("redis", "Redis", ERROR, _safe_error(exc), t0)


async def check_livekit() -> dict:
    """LiveKit credentials + whether phone calls can actually arrive (SIP)."""
    name = "Voice (LiveKit)"
    if not (settings.livekit_url and settings.livekit_api_key and settings.livekit_api_secret):
        return _result("livekit", name, NOT_CONFIGURED, "set LIVEKIT_URL + API key/secret")
    t0 = time.perf_counter()
    try:
        from livekit import api as lk

        lkapi = lk.LiveKitAPI(
            settings.livekit_url, settings.livekit_api_key, settings.livekit_api_secret,
        )
        try:
            rooms = await lkapi.room.list_rooms(lk.ListRoomsRequest())
            trunks = await lkapi.sip.list_inbound_trunk(lk.ListSIPInboundTrunkRequest())
            rules = await lkapi.sip.list_dispatch_rule(lk.ListSIPDispatchRuleRequest())
        finally:
            await lkapi.aclose()
        n_trunks, n_rules = len(trunks.items), len(rules.items)
        detail = f"{len(rooms.rooms)} active room(s); {n_trunks} SIP trunk(s), {n_rules} dispatch rule(s)"
        if not n_trunks or not n_rules:
            # Web voice works; inbound phone calls can't arrive yet.
            detail += " — phone calls not provisioned"
        return _result("livekit", name, OK, detail, t0)
    except Exception as exc:
        return _result("livekit", name, ERROR, _safe_error(exc), t0)


async def check_sms() -> dict:
    provider = (settings.sms_provider or "none").lower()
    name = f"SMS ({provider})"
    t0 = time.perf_counter()
    if provider == "twilio":
        sid, token = settings.twilio_account_sid, settings.twilio_auth_token
        if not (sid and token and settings.twilio_from_number):
            return _result("sms", name, NOT_CONFIGURED, "set TWILIO_ACCOUNT_SID/AUTH_TOKEN/FROM_NUMBER")
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT, auth=(sid, token)) as client:
                r = await client.get(f"https://api.twilio.com/2010-04-01/Accounts/{sid}.json")
                r.raise_for_status()
            status = r.json().get("status", "?")
            return _result("sms", name, OK if status == "active" else ERROR, f"account {status}", t0)
        except Exception as exc:
            return _result("sms", name, ERROR, _safe_error(exc), t0)
    if provider == "bdgateway":
        if not (settings.bd_sms_api_url and settings.bd_sms_api_key):
            return _result("sms", name, NOT_CONFIGURED, "set BD_SMS_API_URL + BD_SMS_API_KEY")
        # No vendor-neutral read endpoint exists; a real balance probe lands
        # with the vendor adapter (Phase 2). Until then: config only.
        return _result("sms", name, OK, "configured (not live-verified until the vendor adapter lands)")
    return _result(
        "sms", name, NOT_CONFIGURED,
        "SMS_PROVIDER=none — OTPs, password resets, confirmations and reminders are NOT sent",
    )


async def check_whatsapp() -> dict:
    name = "WhatsApp (Meta Cloud API)"
    if not (settings.whatsapp_token and settings.whatsapp_phone_id):
        return _result("whatsapp", name, NOT_CONFIGURED, "set WHATSAPP_TOKEN + WHATSAPP_PHONE_ID")
    t0 = time.perf_counter()
    try:
        from tools.whatsapp import _GRAPH

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(
                f"{_GRAPH}/{settings.whatsapp_phone_id}",
                params={"fields": "display_phone_number,verified_name,quality_rating"},
                headers={"Authorization": f"Bearer {settings.whatsapp_token}"},
            )
            r.raise_for_status()
        info = r.json()
        detail = f"{info.get('display_phone_number', '?')} · {info.get('verified_name', '?')}"
        if info.get("quality_rating"):
            detail += f" · quality {info['quality_rating']}"
        if not settings.whatsapp_app_secret:
            detail += " — WHATSAPP_APP_SECRET unset: webhook signatures NOT verified"
        return _result("whatsapp", name, OK, detail, t0)
    except Exception as exc:
        return _result("whatsapp", name, ERROR, _safe_error(exc), t0)


async def check_payments() -> dict:
    provider = (settings.payment_provider or "manual").lower()
    name = f"Payments ({provider})"
    if provider != "sslcommerz":
        return _result("payments", name, OK, "manual provider — no gateway in use")
    if not (settings.sslcommerz_store_id and settings.sslcommerz_store_passwd):
        return _result("payments", name, ERROR, "set SSLCOMMERZ_STORE_ID + SSLCOMMERZ_STORE_PASSWD")
    # SSLCommerz has no read-only auth endpoint; opening a session would create
    # a transaction record, so this stays a config check.
    mode = "sandbox" if settings.sslcommerz_sandbox else "LIVE"
    return _result("payments", name, OK, f"configured ({mode}); IPN → {settings.public_base_url}")


ALL_CHECKS: list[Callable[[], Awaitable[dict]]] = [
    check_database, check_llm, check_embeddings, check_redis,
    check_livekit, check_sms, check_whatsapp, check_payments,
]


async def run_checks(checks: list[Callable[[], Awaitable[dict]]] | None = None) -> list[dict]:
    """Run probes concurrently; a crashing probe becomes an error row."""
    checks = checks or ALL_CHECKS
    results = await asyncio.gather(*(c() for c in checks), return_exceptions=True)
    out = []
    for check, res in zip(checks, results):
        if isinstance(res, BaseException):
            key = check.__name__.removeprefix("check_")
            res = _result(key, key, ERROR, _safe_error(res))
        out.append(res)
    return out


def config_warnings() -> list[str]:
    """Configurations that silently break a patient-facing feature."""
    warnings = []
    if (settings.sms_provider or "none").lower() == "none":
        warnings.append(
            "SMS_PROVIDER=none: phone verification codes, password resets, booking "
            "confirmations and reminders will not reach patients."
        )
    if settings.whatsapp_token and not settings.whatsapp_app_secret:
        warnings.append("WhatsApp is enabled without WHATSAPP_APP_SECRET: inbound webhooks are unauthenticated.")
    if (settings.payment_provider or "manual").lower() == "manual" and settings.booking_fee_default > 0:
        warnings.append("BOOKING_FEE_DEFAULT > 0 with PAYMENT_PROVIDER=manual: fees are never collected.")
    if not (settings.patient_encryption_key or "").strip():
        warnings.append(
            "PATIENT_ENCRYPTION_KEY is unset: patient PII (names, phones, message "
            "bodies, outbox) is stored in plaintext."
        )
    provider, _ = effective_llm_provider()
    wanted = (settings.llm_provider or "ollama").lower()
    if wanted != provider:
        warnings.append(f"LLM_PROVIDER={wanted} has no API key; the agent is falling back to {provider}.")
    return warnings
