"""WhatsApp Business Cloud API webhook.

Inbound text/voice messages drive the same LangGraph agent as chat/SMS. The
ASA number runs in platform mode and verified senders join their account
thread — see api/messaging.py.
Webhook HMAC-SHA256 is verified when WHATSAPP_APP_SECRET is set (recommended
for production; find the secret in Meta App Dashboard → Basic Settings).
"""

from __future__ import annotations

import hashlib
import hmac
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from agent.runner import run_message_turn
from config import settings
from tools.outbox import mark_delivery, seen_inbound
from tools.reminders import handle_reminder_reply
from tools.whatsapp import send_whatsapp_text, transcribe_voice_note
from tools.whatsapp_calls import accept_inbound_call, end_call

from ..messaging import resolve_message_scope, sender_lock

log = logging.getLogger(__name__)

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])


def _verify_meta_signature(raw_body: bytes, sig_header: str) -> None:
    """Raise 403 if the Meta HMAC-SHA256 signature does not match.

    Skipped when WHATSAPP_APP_SECRET is not configured (dev/test mode).
    """
    if not settings.whatsapp_app_secret:
        return
    if not sig_header.startswith("sha256="):
        raise HTTPException(status_code=403, detail="Missing X-Hub-Signature-256 header")
    expected = hmac.new(
        settings.whatsapp_app_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(sig_header[7:], expected):
        raise HTTPException(status_code=403, detail="Invalid webhook signature")


@router.get("/webhook")
async def verify(request: Request):
    """Meta webhook verification handshake."""
    params = request.query_params
    if (
        params.get("hub.mode") == "subscribe"
        and params.get("hub.verify_token") == settings.whatsapp_verify_token
        and settings.whatsapp_verify_token
    ):
        return PlainTextResponse(params.get("hub.challenge", ""))
    return JSONResponse(status_code=403, content={"detail": "verification failed"})


async def _handle_whatsapp_turn(graph, text: str, business_number: str, from_number: str) -> None:
    """Route, run the agent turn, and send the reply. Runs as a background task."""
    try:
        # Two-way reminder: a plain ১/২ reply confirms or cancels the reminded
        # appointment deterministically — no LLM turn.
        reminder_reply = await handle_reminder_reply(from_number, text)
        if reminder_reply:
            await send_whatsapp_text(from_number, reminder_reply)
            return
        scope = await resolve_message_scope("whatsapp", business_number, from_number)
        async with sender_lock(scope["session_id"]):
            result = await run_message_turn(
                graph, scope["session_id"], text,
                channel_identifier=from_number, **scope["turn"],
            )
        if result["reply"]:
            await send_whatsapp_text(from_number, result["reply"], clinic_id=result["clinic_id"])
    except Exception:
        log.exception("WhatsApp background turn failed for %s", from_number)


@router.post("/webhook")
async def inbound(request: Request, background_tasks: BackgroundTasks):
    """Handle an inbound WhatsApp message and reply via the agent.

    Returns 200 immediately and processes the agent turn in the background so
    Meta's 15-second webhook timeout is never exceeded — preventing retry floods
    that would drive the agent turn twice.
    """
    raw_body = await request.body()
    _verify_meta_signature(raw_body, request.headers.get("X-Hub-Signature-256", ""))

    try:
        body = await request.json()
        changes = [
            change.get("value") or {}
            for entry in body.get("entry") or []
            for change in entry.get("changes") or []
        ]
    except (ValueError, AttributeError, TypeError):
        log.warning("Unparseable WhatsApp payload")
        return {"status": "ignored"}

    # Meta batches several messages per delivery and RETRIES deliveries it
    # thinks failed — handle every message, each exactly once.
    queued = 0
    for value in changes:
        business_number = (value.get("metadata") or {}).get("display_phone_number", "")
        for msg in value.get("messages") or []:
            if await _queue_message(request, background_tasks, msg, business_number):
                queued += 1
        # Voice calls (field `calls`): a patient pressed the call button.
        for call in value.get("calls") or []:
            if await _queue_call(background_tasks, call, value.get("metadata") or {}):
                queued += 1
        # Delivery receipts for messages WE sent (wamid = provider_msg_id).
        for st in value.get("statuses") or []:
            outcome = st.get("status")
            if outcome in ("delivered", "read", "failed") and st.get("id"):
                errors = st.get("errors") or [{}]
                await mark_delivery(
                    "whatsapp", st["id"], delivered=outcome != "failed",
                    error=errors[0].get("title") if outcome == "failed" else None,
                )
    return {"status": "ok" if queued else "ignored"}


async def _queue_message(
    request: Request, background_tasks: BackgroundTasks, msg: dict, business_number: str,
) -> bool:
    """Validate, de-duplicate and schedule one inbound message. True if queued."""
    try:
        from_number = msg["from"]
    except (KeyError, TypeError):
        return False
    if await seen_inbound("whatsapp", msg.get("id", "")):
        log.info("WhatsApp retry of %s ignored", msg.get("id"))
        return False

    msg_type = msg.get("type")
    text = (msg.get("text") or {}).get("body", "")
    if not text and msg_type in ("audio", "voice"):
        media = msg.get(msg_type) or {}
        if media.get("id"):
            text = await transcribe_voice_note(media["id"], media.get("mime_type") or "audio/ogg")
    if not text:
        return False

    background_tasks.add_task(
        _handle_whatsapp_turn, request.app.state.graph, text, business_number, from_number,
    )
    return True



async def _queue_call(background_tasks: BackgroundTasks, call: dict, metadata: dict) -> bool:
    """Schedule the connector action for one call event. True if scheduled.

    connect   → answer it with the voice agent (LiveKit AcceptWhatsAppCall)
    terminate → release the LiveKit session (patient hung up)
    The 200 goes back to Meta first; the accept runs right after in the
    background, well within the ringing window.
    """
    call_id, event = call.get("id") or "", (call.get("event") or "").lower()
    if not call_id or event not in ("connect", "terminate"):
        return False
    if await seen_inbound("whatsapp_call", f"{call_id}:{event}"):
        log.info("WhatsApp call %s %s retry ignored", call_id, event)
        return False
    if event == "terminate":
        background_tasks.add_task(end_call, call_id)
        return True
    if (call.get("direction") or "USER_INITIATED").upper() != "USER_INITIATED":
        # Business-initiated calls are completed by whoever dialled them
        # (ConnectWhatsAppCall); ASA does not place outbound calls yet.
        return False
    background_tasks.add_task(
        accept_inbound_call, call,
        metadata.get("phone_number_id", ""), metadata.get("display_phone_number", ""),
    )
    return True
