"""WhatsApp Business Cloud API webhook.

Inbound text/voice messages drive the same LangGraph agent as chat/SMS.
Webhook HMAC-SHA256 is verified when WHATSAPP_APP_SECRET is set (recommended
for production; find the secret in Meta App Dashboard → Basic Settings).
"""

from __future__ import annotations

import hashlib
import hmac
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from agent.runner import run_turn
from config import settings
from tools.reminders import handle_reminder_reply
from tools.whatsapp import send_whatsapp_text, transcribe_voice_note

from ..deps import resolve_channel_clinic

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


async def _handle_whatsapp_turn(
    graph, session_id: str, text: str, clinic_id: int, from_number: str
) -> None:
    """Run the agent turn and send the reply. Executed as a background task."""
    try:
        # Two-way reminder: a plain ১/২ reply confirms or cancels the reminded
        # appointment deterministically — no LLM turn.
        reminder_reply = await handle_reminder_reply(from_number, text)
        if reminder_reply:
            await send_whatsapp_text(from_number, reminder_reply)
            return
        result = await run_turn(graph, session_id, text, clinic_id)
        reply = result.get("reply") or ""
        if reply:
            await send_whatsapp_text(from_number, reply)
    except Exception:
        log.exception("WhatsApp background turn failed for session %s", session_id)


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
        value = body["entry"][0]["changes"][0]["value"]
        messages = value.get("messages") or []
        if not messages:
            return {"status": "ignored"}
        msg = messages[0]
        from_number = msg["from"]
        business_number = (value.get("metadata") or {}).get("display_phone_number", "")
        text = (msg.get("text") or {}).get("body", "")
        msg_type = msg.get("type")
    except (KeyError, IndexError, TypeError):
        log.warning("Unparseable WhatsApp payload")
        return {"status": "ignored"}

    if not text and msg_type in ("audio", "voice"):
        media_id = (msg.get(msg_type) or {}).get("id", "")
        if media_id:
            text = await transcribe_voice_note(media_id)

    if not text:
        return {"status": "ignored"}

    clinic_id = await resolve_channel_clinic("whatsapp", business_number)
    session_id = f"whatsapp-{from_number}"
    background_tasks.add_task(
        _handle_whatsapp_turn, request.app.state.graph, session_id, text, clinic_id, from_number
    )
    return {"status": "ok"}
