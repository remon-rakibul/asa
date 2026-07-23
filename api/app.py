"""FastAPI backend for the appointment-setter agent.

Exposes:
  * the LangGraph agent as a Bangla text chat (HTTP + WebSocket)
  * REST for appointments and the doctor's weekly schedule
  * availability lookup
  * Twilio SMS + WhatsApp webhooks
  * hourly background task for 24-hour appointment reminders

Run:  uvicorn api.app:app --reload --port 8000

Multi-worker note: set WEB_CONCURRENCY>1 to run several uvicorn workers. The
reminder loop is multi-worker safe (Postgres advisory lock); rate limiting is
per-process unless REDIS_URL is set (see api/ratelimit.py).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager

import asyncpg
import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from agent.graph import build_graph
from config import settings
from tools.database import close_pool, get_pool
from tools.reminders import send_pending_reminders
from utils.logging_config import configure_logging, request_id_var

from .ratelimit import get_limiter

from .routes import (
    appointments,
    audit,
    auth,
    chat,
    conversations,
    doctors,
    documents,
    escalations,
    his,
    hospitals,
    integrations,
    messages,
    patient_portal,
    patients,
    payments,
    platform,
    queue,
    reports,
    reviews,
    schedule,
    twilio_sms,
    voice,
    whatsapp,
)

log = logging.getLogger(__name__)


# Arbitrary but stable key identifying "the reminder job" across all workers.
_REMINDER_LOCK_KEY = 0x5265_6D69  # "Remi"
# Separate key for the payment-hold sweep — a distinct job on a much shorter
# cadence, so it needs its own advisory lock (the reminder job's lock is held
# for up to the full hourly cycle and would otherwise block this one).
_PAYMENT_SWEEP_LOCK_KEY = 0x5061_7945  # "PayE"


async def _reminder_loop() -> None:
    """Run send_pending_reminders + the hospital billing sweep every hour.

    Multi-worker safe: a Postgres advisory lock ensures exactly one process
    runs each cycle — the others find the lock taken and skip.
    (reminder_sent_at additionally makes the send itself idempotent.)
    """
    from tools.database import sweep_hospital_billing

    while True:
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                got = await conn.fetchval(
                    "SELECT pg_try_advisory_lock($1)", _REMINDER_LOCK_KEY
                )
                if got:
                    try:
                        n = await send_pending_reminders()
                        if n:
                            log.info("Reminder loop: sent %d reminder(s)", n)
                        changed = await sweep_hospital_billing()
                        if changed:
                            log.info("Billing sweep: %d hospital(s) changed state", changed)
                    finally:
                        await conn.fetchval(
                            "SELECT pg_advisory_unlock($1)", _REMINDER_LOCK_KEY
                        )
        except Exception:
            log.exception("Reminder loop error")
        await asyncio.sleep(3600)


async def _payment_sweep_loop() -> None:
    """Cancel expired pending_payment holds every 60 seconds.

    A held slot has a short TTL (PAYMENT_TTL_MINUTES, default 15) — waiting
    for the hourly reminder cadence would leave a slot needlessly blocked
    for up to an hour after the patient abandoned payment. Same
    multi-worker-safe advisory-lock pattern as the reminder loop.
    """
    from tools.database import sweep_expired_payments

    while True:
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                got = await conn.fetchval(
                    "SELECT pg_try_advisory_lock($1)", _PAYMENT_SWEEP_LOCK_KEY
                )
                if got:
                    try:
                        n = await sweep_expired_payments()
                        if n:
                            log.info("Payment sweep: released %d expired hold(s)", n)
                    finally:
                        await conn.fetchval(
                            "SELECT pg_advisory_unlock($1)", _PAYMENT_SWEEP_LOCK_KEY
                        )
        except Exception:
            log.exception("Payment sweep loop error")
        await asyncio.sleep(60)


def _init_sentry() -> None:
    if not settings.sentry_dsn:
        return
    try:
        import sentry_sdk
        sentry_sdk.init(dsn=settings.sentry_dsn, traces_sample_rate=0.1)
        log.info("Sentry initialised")
    except Exception:
        log.exception("Sentry init failed (continuing without it)")


def _warn_missing_webhook_secrets() -> None:
    if not settings.whatsapp_app_secret:
        log.warning(
            "WHATSAPP_APP_SECRET is not set — WhatsApp webhook signature verification "
            "is DISABLED. Any HTTP client can forge inbound messages."
        )
    if not settings.twilio_auth_token:
        log.warning(
            "TWILIO_AUTH_TOKEN is not set — Twilio webhook signature verification "
            "is DISABLED. Any HTTP client can forge inbound SMS messages."
        )


async def _warmup_llm() -> None:
    """Load the Ollama model into RAM at startup so the first patient request isn't slow.

    Runs as a background task so it doesn't block server readiness. Uses keep_alive=-1
    (set in ChatOllama) so the model stays loaded until the process exits.
    """
    try:
        from agent.nodes import _llm_booking_only
        llm = _llm_booking_only()
        from langchain_core.messages import HumanMessage
        await llm.ainvoke([HumanMessage(content="hello")])
        log.info("LLM warmup complete — model loaded into RAM")
    except Exception as exc:
        log.warning("LLM warmup failed (first request will be slow): %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(level=settings.log_level, as_json=settings.log_json)
    _init_sentry()
    _warn_missing_webhook_secrets()
    app.state.graph = await build_graph()
    await get_pool()
    asyncio.create_task(_warmup_llm())
    reminder_task = asyncio.create_task(_reminder_loop())
    payment_sweep_task = asyncio.create_task(_payment_sweep_loop())
    try:
        yield
    finally:
        reminder_task.cancel()
        payment_sweep_task.cancel()
        # asyncpg pool.close() waits for every acquired connection to be
        # released — an open SSE chat stream would block shutdown (and wedge
        # uvicorn --reload) forever. Time-bound it; terminate() force-closes.
        try:
            await asyncio.wait_for(close_pool(), timeout=5)
        except (asyncio.TimeoutError, Exception):
            from tools.database import terminate_pool
            terminate_pool()


app = FastAPI(title="Appointment Setter Agent API", version="1.0.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Per-IP rate limiting (fixed 60-second window).
# Backend chosen in api/ratelimit.py: in-process by default, Redis when
# REDIS_URL is set (required for accurate limits with multiple workers).
# ---------------------------------------------------------------------------
_PATIENT_PREFIXES = ("/chat", "/patient/chat", "/twilio", "/whatsapp")
_LOGIN_PATHS = ("/auth/login", "/patient/login", "/hospitals/signup")


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    from .deps import client_ip

    path = request.url.path
    # Proxy-aware: behind a reverse proxy (TRUST_PROXY_HEADERS=true) this is the
    # real client IP from X-Forwarded-For, not the proxy's — otherwise every
    # client would share one bucket and trip each other's rate limits.
    ip = client_ip(request) or "unknown"
    limiter = get_limiter()

    if path in _LOGIN_PATHS:
        if not await limiter.allow("login", ip, settings.admin_login_rate_limit_per_min):
            log.warning("Rate limit hit for %s on %s", ip, path)
            return JSONResponse(status_code=429, content={"detail": "Too many login attempts"})

    elif any(path.startswith(p) for p in _PATIENT_PREFIXES):
        if not await limiter.allow("patient", ip, settings.patient_rate_limit_per_min):
            log.warning("Rate limit hit for %s on %s", ip, path)
            return JSONResponse(status_code=429, content={"detail": "Too many requests"})

    return await call_next(request)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    token = request_id_var.set(rid)
    try:
        response = await call_next(request)
    finally:
        request_id_var.reset(token)
    response.headers["X-Request-ID"] = rid
    return response


# CORS is registered LAST so it is the OUTERMOST middleware: every response —
# including the rate limiter's short-circuited 429 and CORS preflight OPTIONS —
# passes back out through it and gets the Access-Control-Allow-Origin header.
# Registered before the limiter (innermost, as it was originally), a browser
# couldn't read a 429 at all (it surfaced as an opaque network error), and
# preflight OPTIONS would have burned rate-limit tokens.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list(),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(asyncpg.exceptions.DataError)
async def data_error_handler(request: Request, exc: asyncpg.exceptions.DataError):
    """A client-supplied path/query value didn't fit the column type it was
    cast to — e.g. a non-UUID appointment id hitting `id = $1::uuid`, a
    non-numeric filter, or a bad datetime. That's a malformed request, not a
    server fault: return 400 instead of leaking a 500. (Postgres has already
    aborted the statement; no partial write survives.)"""
    log.info("Bad request value on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(status_code=400, content={"detail": "Invalid value in request"})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(appointments.router)
app.include_router(schedule.router)
app.include_router(twilio_sms.router)
app.include_router(whatsapp.router)
app.include_router(integrations.router)
app.include_router(conversations.router)
app.include_router(escalations.router)
app.include_router(messages.router)
app.include_router(doctors.router)
app.include_router(reviews.router)
# Hospital-mode routes
app.include_router(hospitals.router)
app.include_router(documents.router)
app.include_router(patient_portal.router)
app.include_router(payments.router)
app.include_router(platform.router)
app.include_router(voice.router)
app.include_router(patients.router)
app.include_router(queue.router)
app.include_router(reports.router)
app.include_router(audit.router)
app.include_router(his.router)


@app.get("/health")
async def health() -> dict:
    """Deep health check: verifies DB connectivity and Ollama reachability.
    Returns 200 with status='ok' only when all dependencies are healthy.
    """
    checks: dict[str, str] = {}

    # Database
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {exc}"

    # Ollama
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{settings.ollama_base_url}/api/tags")
        checks["ollama"] = "ok" if r.status_code == 200 else f"http {r.status_code}"
    except Exception as exc:
        checks["ollama"] = f"error: {exc}"

    all_ok = all(v == "ok" for v in checks.values())
    return {"status": "ok" if all_ok else "degraded", "checks": checks}
